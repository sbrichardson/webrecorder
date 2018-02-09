import os
import json

from bottle import request, HTTPError
from os.path import expandvars

from webrecorder.webreccork import ValidationException
from webrecorder.basecontroller import BaseController

from six.moves.urllib.parse import quote


# ============================================================================
LOGIN_PATH = '/_login'
LOGIN_MODAL_PATH = '/_login_modal'

LOGOUT_PATH = '/_logout'
CREATE_PATH = '/_create'

REGISTER_PATH = '/_register'
VAL_REG_PATH = '/_valreg/<reg>'
VAL_REG_PATH_POST = '/_valreg'
INVITE_PATH = '/_invite'

FORGOT_PATH = '/_forgot'

RESET_POST = '/_resetpassword'
RESET_PATH = '/_resetpassword/<resetcode>'
RESET_PATH_FILL = '/_resetpassword/{0}?username={1}'

UPDATE_PASS_PATH = '/_updatepassword'
SETTINGS = '/_settings'


# ============================================================================
class LoginController(BaseController):
    def __init__(self, *args, **kwargs):
        super(LoginController, self).__init__(*args, **kwargs)
        config = kwargs['config']
        self.cork = kwargs['cork']

        self.announce_list = os.environ.get('ANNOUNCE_MAILING_LIST_ENDPOINT', False)
        invites = expandvars(config.get('invites_enabled', 'true')).lower()
        self.invites_enabled = invites in ('true', '1', 'yes')

    def init_routes(self):

        @self.app.get('/api/v1/load_auth')
        def load_auth():
            sesh = self.user_manager.get_session()

            if sesh:
                # current user
                u = self.get_user(user=sesh.curr_user)

                return {
                    'username': sesh.curr_user,
                    'role': sesh.curr_role,
                    'anon': u.is_anon(),
                    'coll_count': u.num_collections(),
                }

            return {'username': None, 'role': None, 'anon': None}

        @self.app.post('/api/v1/login')
        def login():
            """Authenticate users"""
            data = request.json
            username = data.get('username', '')
            password = data.get('password', '')

            if not self.user_manager.cork.login(username, password):
                return HTTPError(status=401)

            sesh = self.user_manager.get_session()
            u = self.get_user(user=sesh.curr_user)
            sesh.curr_user = username
            sesh.curr_role = u['role']

            remember_me = (data.get('remember_me') in ('1', 'on'))
            sesh.logged_in(remember_me)

            return {
                'username': sesh.curr_user,
                'role': sesh.curr_role,
                'coll_count': u.num_collections(),
                'anon': u.is_anon()
            }

        @self.app.get('/api/v1/username_check')
        def test_username():
            """async precheck username availability on signup form"""
            username = request.query.username

            try:
                assert username not in self.user_manager.RESTRICTED_NAMES
                user = self.user_manager.all_users[username]
                return {'available': True}
            except:
                return {'available': False}

        @self.app.post('/api/v1/updatepassword')
        @self.user_manager.auth_view()
        def update_password():
            curr_password = self.post_get('currPass')
            password = self.post_get('newPass')
            confirm_password = self.post_get('newPass2')

            try:
                self.user_manager.update_password(curr_password, password,
                                                  confirm_password)
                return {}
            except ValidationException as ve:
                return self._raise_error(403, str(ve), api=True)

        # Login/Logout
        # ============================================================================
        @self.app.get(LOGIN_PATH)
        @self.jinja2_view('login.html')
        def login():
            self.redirect_home_if_logged_in()
            resp = {}
            self.fill_anon_info(resp)
            return resp

        @self.app.get(LOGIN_MODAL_PATH)
        @self.jinja2_view('login_modal.html')
        def login_modal():
            #self.redirect_home_if_logged_in()
            resp = {}
            self.fill_anon_info(resp)
            return resp

        @self.app.post(LOGIN_PATH)
        def login_post():
            self.redirect_home_if_logged_in()

            """Authenticate users"""
            username = self.post_get('username')
            password = self.post_get('password')

            try:
                move_info = self.user_manager.get_move_temp_info(request.forms)
            except ValidationException as ve:
                self.flash_message('Login Failed: ' + str(ve))
                self.redirect('/')
                return

            # if a collection is being moved, auth user
            # and then check for available space
            # if not enough space, don't continue with login
            if move_info and (self.cork.
                              is_authenticate(username, password)):

                if not self.user_manager.has_space_for_new_collection(username,
                                                           move_info['from_user'],
                                                           'temp'):
                    self.flash_message('Sorry, not enough space to import this Temporary Collection into your account.')
                    self.redirect('/')
                    return

            if not self.cork.login(username, password):
                self.flash_message('Invalid Login. Please Try Again')
                redir_to = LOGIN_PATH
                self.redirect(redir_to)

            if move_info:
                user = self.get_user(user=username)
                the_collection = self.user_manager.move_temp_coll(user, move_info)
                if the_collection:
                    self.flash_message('Collection <b>{0}</b> created!'.format(move_info['to_title']), 'success')

            remember_me = (self.post_get('remember_me') == '1')
            sesh = self.get_session()

            # mark as logged-in
            sesh.log_in(username, remember_me)

            temp_prefix = self.user_manager.temp_prefix

            redir_to = request.headers.get('Referer')
            host = self.get_host()

            if redir_to and redir_to.startswith(host):
                redir_to = redir_to[len(host):]

            if not redir_to or redir_to.startswith(('/' + temp_prefix,
                                                    '/_')):
                redir_to = self.get_path(username)

            if self.content_host:
                path = '/_clear_session?path=' + quote(redir_to)
                self.redir_host(self.content_host, path)
            else:
                self.redirect(redir_to)

        @self.app.get(LOGOUT_PATH)
        def logout():
            redir_to = '/'

            if self.content_host:
                path = '/_clear_session?path=' + quote(redir_to)
                url = request.environ['wsgi.url_scheme'] + '://' + self.content_host
                url += path
                redir_to = url

            self.cork.logout(success_redirect=redir_to, fail_redirect=redir_to)


        # Register/Invite/Confirm
        # ============================================================================
        @self.app.get(REGISTER_PATH)
        @self.jinja2_view('register.html')
        def register():
            self.redirect_home_if_logged_in()

            if not self.invites_enabled:
                resp = {'email': '',
                        'skip_invite': True}

                self.fill_anon_info(resp)

                return resp

            invitecode = request.query.getunicode('invite', '')
            email = ''

            try:
                email = self.user_manager.is_valid_invite(invitecode)
            except ValidationException as ve:
                self.flash_message(str(ve))

            return { 'email': email,
                     'invite': invitecode}

        @self.app.post(INVITE_PATH)
        def invite_post():
            self.redirect_home_if_logged_in()

            email = self.post_get('email')
            name = self.post_get('name')
            desc = self.post_get('desc')
            if self.user_manager.save_invite(email, name, desc):
                self.flash_message('Thank you for your interest! We will send you an invite to try webrecorder.io soon!', 'success')
                self.redirect('/')
            else:
                self.flash_message('Oops, something went wrong, please try again')
                self.redirect(REGISTER_PATH)


        @self.app.post(REGISTER_PATH)
        def register_post():
            self.redirect_home_if_logged_in()

            redir_to = REGISTER_PATH

            data = dict(request.forms)

            msg, redir_extra = self.user_manager.register_user(data, self.get_host())

            if 'success' in msg:
                self.flash_message(msg['success'])
                self.redirect('/')

            else:
                redir_to += redir_extra
                self.flash_message(list(msg.values())[0], 'warning')
                self.redirect(redir_to)

        @self.app.get(VAL_REG_PATH)
        @self.jinja2_view('val_reg.html')
        def val_reg(reg):
            return {'reg': reg}

        # Validate Registration
        @self.app.post(VAL_REG_PATH_POST)
        def val_reg_post():
            self.redirect_home_if_logged_in()

            reg = self.post_get('reg', '')

            cookie = request.environ.get('webrec.request_cookie', '')

            result = self.user_manager.validate_registration(reg, cookie)

            if 'registered' in result:
                msg = '<b>{0}</b>, you are now logged in!'
                if result.get('first_coll_name') == 'default-collection':
                    msg += ' The <b>{1}</b> collection has been created for you, and you can begin recording by entering a url below!'
                else:
                    msg += ' The <b>{1}</b> collection has been permanently saved for you, and you can continue recording by entering a url below!'

                self.flash_message(msg.format(result['registered'], result.get('first_coll_name')), 'success')
                self.redirect('/')

            if result['error'] == 'invalid':
                self.flash_message('Registration Not Accepted', 'error')
                self.redirect(REGISTER_PATH)

            elif result['error'] == 'already_registered':
                self.flash_message('The user <b>{0}</b> is already registered. \
    If this is you, please login or click forgot password, \
    or register a new account.'.format(username))
                self.redirect(LOGIN_PATH)

            self.redirect(REGISTER_PATH)

        # Forgot Password
        # ============================================================================
        @self.app.get(FORGOT_PATH)
        @self.jinja2_view('forgot.html')
        def forgot():
            self.redirect_home_if_logged_in()
            return {}


        @self.app.post(FORGOT_PATH)
        def forgot_submit():
            self.redirect_home_if_logged_in()

            email = self.post_get('email')
            username = self.post_get('username')
            host = self.get_host()

            try:
                self.cork.send_password_reset_email(username=username,
                                          email_addr=email,
                                          subject='webrecorder.io password reset confirmation',
                                          email_template='webrecorder/templates/emailreset.html',
                                          host=host)

                self.flash_message('A password reset e-mail has been sent to your e-mail!', 'success')
                redir_to = '/'
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.flash_message(str(e))
                redir_to = FORGOT_PATH

            self.redirect(redir_to)


        # Reset Password
        # ============================================================================
        @self.app.get(RESET_PATH)
        @self.jinja2_view('reset.html')
        def resetpass(resetcode):
            self.redirect_home_if_logged_in()

            try:
                username = request.query['username']
                result = {'username': username,
                          'resetcode': resetcode}

            except Exception as e:
                print(e)
                self.flash_message('Invalid password reset attempt. Please try again')
                self.redirect(FORGOT_PATH)

            return result


        @self.app.post(RESET_POST)
        def do_reset():
            self.redirect_home_if_logged_in()

            username = self.post_get('username')
            resetcode = self.post_get('resetcode')
            password = self.post_get('password')
            confirm_password = self.post_get('confirmpassword')

            try:
                self.user_manager.validate_password(password, confirm_password)

                self.user_manager.cork.reset_password(resetcode, password)

                self.flash_message('Your password has been successfully reset! \
    You can now <b>login</b> with your new password!', 'success')

                redir_to = LOGIN_PATH

            except ValidationException as ve:
                self.flash_message(str(ve))
                redir_to = RESET_PATH_FILL.format(resetcode, username)

            except Exception as e:
                self.flash_message('Invalid password reset attempt. Please try again')
                redir_to = FORGOT_PATH

            self.redirect(redir_to)


        # Update Password
        @self.app.post(UPDATE_PASS_PATH)
        def update_password():
            self.cork.require(role='archivist', fail_redirect=LOGIN_PATH)

            curr_password = self.post_get('curr_password')
            password = self.post_get('password')
            confirm_password = self.post_get('confirmpassword')

            try:
                self.user_manager.update_password(curr_password, password, confirm_password)
                self.flash_message('Password Updated', 'success')
            except ValidationException as ve:
                self.flash_message(str(ve))

            username = self.access.session_user.name
            self.redirect(self.get_path(username) + SETTINGS)

    def redirect_home_if_logged_in(self):
        sesh = self.get_session()

        if sesh.curr_user:
            self.flash_message('You are already logged in as <b>{0}</b>'.format(sesh.curr_user))
            self.redirect('/')


