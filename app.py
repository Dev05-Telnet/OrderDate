import flask 
from bigcommerce.api import BigcommerceApi
import dotenv
from datetime import datetime
import requests
import json
import os
from flask_sqlalchemy import SQLAlchemy


# do __name__.split('.')[0] if initialising from a file not at project root
app = flask.Flask(__name__)

# Look for a .env file
if os.path.exists('.env'):
    dotenv.load_dotenv('.env')

# Load configuration from environment, with defaults
app.config['DEBUG'] = True if os.getenv('DEBUG') == 'True' else False
app.config['LISTEN_HOST'] = os.getenv('LISTEN_HOST', '0.0.0.0')
app.config['LISTEN_PORT'] = int(os.getenv('LISTEN_PORT', '5000'))
app.config['APP_URL'] = os.getenv('APP_URL', 'http://localhost:5000')  # must be https to avoid browser issues
app.config['APP_CLIENT_ID'] = os.getenv('APP_CLIENT_ID')
app.config['APP_CLIENT_SECRET'] = os.getenv('APP_CLIENT_SECRET')
app.config['SESSION_SECRET'] = os.urandom(64)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///orderman.sqlite')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = app.config['DEBUG']

# Setup secure cookie secret
app.secret_key = app.config['SESSION_SECRET']

# Setup db
db = SQLAlchemy(app)

# Helper for template rendering
def render(template, context):
    return flask.render_template(template, **context)

@app.route('/')
def index():
    storeId = flask.request.values.get('storeId')
    
    return render('index.html',{'storeId':storeId})

@app.route('/order',methods = ['POST', 'GET'])
def orderview():

    storeId = flask.request.values.get('storeId')
    store = Store.query.filter_by(id=storeId).first()
    
    if (flask.request.method == 'GET'):
        if 'orderId' in flask.request.args :
            orderId = flask.request.args['orderId']
            url = 'https://api.bigcommerce.com/stores/'+store.store_hash+'/v2/orders/'+str(orderId)
            header = {"X-Auth-Token": store.access_token,"Accept":"application/json","Content-Type":"application/json"}
            x = requests.get(url,headers = header)
            if x.status_code == 200:
                o = json.loads(x.text)
                o['storeId'] = storeId
                return render('order.html',o)
            if x.status_code == 404:
                o = json.loads(x.text)
                return o[0]['message']
            if x.status_code == 401 or x.status_code == 403:
                o = json.loads(x.text)
                return o['title']
            return "Unknown Status Code"
        else:
            return render('index.html',{'msg':'Please provide a Order Id','storeId':storeId})
    else:
        newdate = flask.request.values.get('newdate')
        orderId = flask.request.values.get('orderId')

        createdAt = datetime.strptime(newdate, '%Y-%m-%dT%H:%M').strftime('%d %b %Y %H:%M:%S')

        data = json.dumps({'date_created':createdAt + ' +0000'})

        url = 'https://api.bigcommerce.com/stores/'+store.store_hash+'/v2/orders/'+str(orderId)
        header = {"X-Auth-Token": store.access_token,"Accept":"application/json","Content-Type":"application/json"}
        x = requests.put(url, data =data, headers = header)

        if x.status_code == 200:
            return render('index.html',{'msg':'Order Succesfully updated','storeId':storeId})
        else: 
            return render('index.html',{'msg':'Some Error occured while updating -Status Code '+str(x.status_code),'storeId':storeId})

#
# Error handling and helpers
#
def error_info(e):
    content = ""
    try:  # it's probably a HttpException, if you're using the bigcommerce client
        content += str(e.headers) + "<br>" + str(e.content) + "<br>"
        req = e.response.request
        content += "<br>Request:<br>" + req.url + "<br>" + str(req.headers) + "<br>" + str(req.body)
    except AttributeError as e:  # not a HttpException
        content += "<br><br> (This page threw an exception: {})".format(str(e))
    return content


@app.errorhandler(500)
def internal_server_error(e):
    content = "Internal Server Error: " + str(e) + "<br>"
    content += error_info(e)
    return content, 500


@app.errorhandler(400)
def bad_request(e):
    content = "Bad Request: " + str(e) + "<br>"
    content += error_info(e)
    return content, 400


# Helper for template rendering
def render(template, context):
    return flask.render_template(template, **context)


def client_id():
    return app.config['APP_CLIENT_ID']


def client_secret():
    return app.config['APP_CLIENT_SECRET']

#
# OAuth pages
#

# Bigcommerce api endpoint
@app.route('/bigcommerce/callback')
def auth_callback():
    # Put together params for token request
    code = flask.request.args['code']
    context = flask.request.args['context']
    scope = flask.request.args['scope']
    store_hash = context.split('/')[1]
    redirect = app.config['APP_URL'] + flask.url_for('auth_callback')

    # Fetch a permanent oauth token. This will throw an exception on error,
    # which will get caught by our error handler above.
    client = BigcommerceApi(client_id=client_id(), store_hash=store_hash)
    token = client.oauth_fetch_token(client_secret(), code, context, scope, redirect)
    
    access_token = token['access_token']

      # Create or update store
    store = Store.query.filter_by(store_hash=store_hash).first()
    if store is None:
        store = Store(store_hash, access_token, scope)
        db.session.add(store)
        db.session.commit()
    else:
        store.access_token = access_token
        store.scope = scope
        db.session.add(store)
        db.session.commit()

    url = app.config['APP_URL']+'?storeId='+str(store.id)
    return flask.redirect(url)


# The Load URL. See https://developer.bigcommerce.com/api/load
@app.route('/bigcommerce/load')
def load():
    # Decode and verify payload
    payload = flask.request.args['signed_payload']
    user_data = BigcommerceApi.oauth_verify_payload(payload, client_secret())
    if user_data is False:
        return "Payload verification failed!", 401

    store_hash = user_data['store_hash']

    # Lookup store
    store = Store.query.filter_by(store_hash=store_hash).first()
    if store is None:
        return "Store not found!", 401
    
    url = app.config['APP_URL']+'?storeId='+str(store.id)
    return flask.redirect(url)


# flask data models
class Store(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    store_hash = db.Column(db.String(16), nullable=False, unique=True)
    access_token = db.Column(db.String(128), nullable=False)
    scope = db.Column(db.Text(), nullable=False)
    
    def __init__(self, store_hash, access_token, scope):
        self.store_hash = store_hash
        self.access_token = access_token
        self.scope = scope

    def __repr__(self):
        return '<Store id=%d store_hash=%s access_token=%s scope=%s>' \
               % (self.id, self.store_hash, self.access_token, self.scope)


if __name__ == "__main__":
  app.run()

