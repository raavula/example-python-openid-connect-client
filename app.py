##########################################################################
# Copyright 2016 Curity AB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##########################################################################

import json
import sys
import urllib2
from flask import redirect, request, render_template, session, Flask
from jwkest import BadSignature
from urlparse import urlparse

from client import Client
from tools import decode_token, generate_random_string
from validator import JwtValidator

_app = Flask(__name__)


class UserSession(object):
    access_token = None
    refresh_token = None
    id_token = None
    access_token_json = None
    id_token_json = None
    name = None


@_app.route('/')
def index():
    """
    :return: the index page, with the tokens if set.
    """
    user = _session_store.get(session.get('session_id', None), UserSession())
    if not user:
        return render_template('index.html', server_name=urlparse(_config['authorization_endpoint']).netloc,
                               title='Hello sweet world!')
    if user.id_token:
        user.id_token_json = decode_token(user.id_token)
    if user.access_token:
        user.access_token_json = decode_token(user.access_token)

    return render_template('index.html', server_name=urlparse(_config['authorization_endpoint']).netloc,
                           user=user)


@_app.route('/login')
def start_code_flow():
    """
    :return: redirects to the authorization server with the appropriate parameters set.
    """
    login_url = _client.get_authn_req_url(session)
    return redirect(login_url)


@_app.route('/logout')
def logout():
    """
    Logout clears the session, along with the tokens
    :return: redirects to /
    """
    if 'session_id' in session:
        del _session_store[session['session_id']]
    session.clear()
    if 'logout_endpoint' in _config:
        print "Logging out against", _config['logout_endpoint']
        return redirect(_config['logout_endpoint'] + '?redirect_uri=https://localhost:5443/')
    return redirect('/')


@_app.route('/refresh')
def refresh():
    """
    Refreshes the access token using the refresh token
    :return: redirects to /
    """
    user = _session_store.get(session['session_id'])
    try:
        token_data = _client.refresh(user.refresh_token)
    except Exception as e:
        return create_error("Could not refresh Access Token: %s" % e.message)
    user.access_token = token_data['access_token']
    user.refresh_token = token_data['refresh_token']
    return redirect('/')


@_app.route('/revoke')
def revoke():
    """
    Revokes the access and refresh token and clears the sessions
    :return: redirects to /
    """
    if 'session_id' in session:
        user = _session_store.get(session['session_id'])
        if not user:
            redirect('/')
        if user.access_token:
            try:
                _client.revoke(user.access_token)
            except urllib2.URLError as ue:
                return create_error('Could not revoke token: ' + ue.message)
            user.access_token = None

        if user.refresh_token:
            try:
                _client.revoke(user.refresh_token)
            except urllib2.URLError as ue:
                return create_error('Could not revoke refresh token: ' + ue.message)
            user.refresh_token = None
            user.access_token = None

        user.id_token = None
    return redirect('/')


@_app.route('/callback')
def oauth_callback():
    """
    Called when the resource owner is returning from the authorization server
    :return:redirect to / with user info stored in the session.
    """
    if 'state' not in session or session['state'] != request.args['state']:
        raise Exception('Missing or invalid state')

    if 'code' not in request.args:
        raise Exception('No code in response')

    try:
        token_data = _client.get_token(request.args['code'])
    except Exception as e:
        return create_error('Could not fetch token: %s' % e.message)
    session.pop('state', None)

    # Store in basic server session, since flask session use cookie for storage
    user = UserSession()

    if 'access_token' in token_data:
        user.access_token = token_data['access_token']

    if 'id_token' in token_data:
        # validate JWS; signature, aud and iss.
        # Token type, access token, ref-token and JWT
        if 'issuer' not in _config:
            return create_error('Could not validate token: no issuer configured')

        if not _jwt_validator:
            return create_error('Could not validate token: no jwks_uri configured')
        try:
            _jwt_validator.validate(token_data['id_token'], _config['issuer'], _config['client_id'])
        except BadSignature as bs:
            return create_error('Could not validate token: %s' % bs.message)
        except Exception as ve:
            return create_error("Unexpected exception: %s" % ve.message)

        user.id_token = token_data['id_token']

    if 'refresh_token' in token_data:
        user.refresh_token = token_data['refresh_token']

    session['session_id'] = generate_random_string()
    _session_store[session['session_id']] = user

    return redirect('/')


def create_error(message):
    """
    Print the error and output it to the page
    :param message:
    :return: redirects to index.html with the error message
    """
    print message
    if _app:
        return render_template('index.html', error=message)


def load_config():
    """
    Load config from the file given by argument, or settings.json
    :return:
    """
    if len(sys.argv) > 1:
        filename = sys.argv[1]
    else:
        filename = 'settings.json'
    print 'Loading settings from %s' % filename
    config = json.loads(open(filename).read())

    return config


if __name__ == '__main__':
    # load the config
    _config = load_config()

    _client = Client(_config)

    # load the jwk set.
    if 'jwks_uri' in _config:
        _jwt_validator = JwtValidator(_config)
    else:
        print 'Found no url to JWKS, will not be able to validate JWT signature.'

    # create a session store
    _session_store = {}
    # initiate the app
    _app.secret_key = generate_random_string()

    # some default values
    _debug = 'debug' in _config and _config['debug']
    if 'port' in _config:
        _port = _config['port']
    else:
        _port = 5443

    _app.run('0.0.0.0', debug=_debug, port=_port, ssl_context=('keys/localhost.pem', 'keys/localhost.pem'))