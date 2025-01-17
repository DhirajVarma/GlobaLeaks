# -*- coding: utf-8 -*-
#
# Handlers dealing with platform authentication
from datetime import datetime
from random import SystemRandom
from twisted.internet.defer import inlineCallbacks, returnValue
from globaleaks.handlers.admin.node import db_admin_serialize_node
from globaleaks.handlers.admin.notification import db_get_notification
from globaleaks.handlers.base import BaseHandler
from globaleaks.models import InternalTip, User, UserTenant, WhistleblowerTip
from globaleaks.orm import transact
from globaleaks.rest import errors, requests
from globaleaks.sessions import Sessions
from globaleaks.settings import Settings
from globaleaks.state import State
from globaleaks.twofactor import TwoFactorTokens
from globaleaks.utils.crypto import GCE
from globaleaks.utils.ip import check_ip
from globaleaks.utils.log import log
from globaleaks.utils.templating import Templating
from globaleaks.utils.utility import datetime_now, datetime_null, deferred_sleep


def random_login_delay():
    """
    in case of failed_login_attempts introduces
    an exponential increasing delay between 0 and 42 seconds

        the function implements the following table:
            ----------------------------------
           | failed_attempts |      delay     |
           | x < 5           | 0              |
           | 5               | random(5, 25)  |
           | 6               | random(6, 36)  |
           | 7               | random(7, 42)  |
           | 8 <= x <= 42    | random(x, 42)  |
           | x > 42          | 42             |
            ----------------------------------
    """
    failed_attempts = Settings.failed_login_attempts

    if failed_attempts >= 5:
        n = failed_attempts * failed_attempts

        min_sleep = failed_attempts if failed_attempts < 42 else 42
        max_sleep = n if n < 42 else 42

        return SystemRandom().randint(min_sleep, max_sleep)

    return 0


def connection_check(client_ip, tid, role, client_using_tor):
    ip_filter = State.tenant_cache[tid]['ip_filter'].get(role)
    if (ip_filter and not check_ip(client_ip, ip_filter)):
        raise errors.AccessLocationInvalid

    https_allowed = State.tenant_cache[tid]['https_allowed'].get(role)
    if (not https_allowed and not client_using_tor):
        raise errors.TorNetworkRequired


@transact
def login_whistleblower(session, tid, receipt):
    """
    login_whistleblower returns a session
    """
    x = None

    algorithms = [x[0] for x in session.query(WhistleblowerTip.hash_alg).filter(WhistleblowerTip.tid == tid).distinct()]
    if algorithms:
        hashes = []
        for alg in algorithms:
            hashes.append(GCE.hash_password(receipt, State.tenant_cache[tid].receipt_salt, alg))

        x  = session.query(WhistleblowerTip, InternalTip) \
                    .filter(WhistleblowerTip.receipt_hash.in_(hashes),
                            WhistleblowerTip.tid == tid,
                            InternalTip.id == WhistleblowerTip.id,
                            InternalTip.tid == WhistleblowerTip.tid).one_or_none()

    if x is None:
        log.debug("Whistleblower login: Invalid receipt")
        Settings.failed_login_attempts += 1
        raise errors.InvalidAuthentication

    wbtip = x[0]
    itip = x[1]

    itip.wb_last_access = datetime_now()

    crypto_prv_key = ''
    if State.tenant_cache[1].encryption and wbtip.crypto_prv_key:
        user_key = GCE.derive_key(receipt.encode('utf-8'), State.tenant_cache[tid].receipt_salt)
        crypto_prv_key = GCE.symmetric_decrypt(user_key, wbtip.crypto_prv_key)

    return Sessions.new(tid, wbtip.id, 'whistleblower', False, crypto_prv_key)


@transact
def login(session, tid, username, password, authcode, client_using_tor, client_ip):
    """
    login returns a session
    """
    user = None

    users = session.query(User).filter(User.username == username,
                                       User.state != u'disabled',
                                       UserTenant.user_id == User.id,
                                       UserTenant.tenant_id == tid).distinct()
    for u in users:
        if GCE.check_password(u.hash_alg, password, u.salt, u.password):
            user = u
            break

        # Fix for issue: https://github.com/globaleaks/GlobaLeaks/issues/2563
        if State.tenant_cache[1].creation_date < 1551740400:
            u_password = 'b\'' + u.password + '\''
            if GCE.check_password(u.hash_alg, password, u.salt, u_password):
                user = u
                break

    if user is None:
        log.debug("Login: Invalid credentials")
        Settings.failed_login_attempts += 1
        raise errors.InvalidAuthentication

    connection_check(client_ip, tid, user.role, client_using_tor)

    if State.tenant_cache[1].two_factor_auth and user.last_login != datetime_null():
        token = TwoFactorTokens.get(user.id)

        if token is not None and authcode != '':
            if token.token == authcode:
                TwoFactorTokens.revoke(user.id)
            else:
                raise errors.InvalidTwoFactorAuthCode

        elif token is None and authcode == '':
            token = TwoFactorTokens.new(user.id)

            data = {
                'type': '2fa',
                'authcode': str(token.token)
            }

            data['node'] = db_admin_serialize_node(session, tid, user.language)
            data['notification'] = db_get_notification(session, tid, user.language)

            subject, body = Templating().get_mail_subject_and_body(data)
            State.sendmail(1, user.mail_address, subject, body)
            raise errors.TwoFactorAuthCodeRequired
        else:
            raise errors.TwoFactorAuthCodeRequired

    user.last_login = datetime_now()

    crypto_prv_key = ''
    if State.tenant_cache[1].encryption and user.crypto_prv_key:
        user_key = GCE.derive_key(password.encode('utf-8'), user.salt)
        crypto_prv_key = GCE.symmetric_decrypt(user_key, user.crypto_prv_key)

    return Sessions.new(tid, user.id, user.role, user.password_change_needed, crypto_prv_key)


@transact
def check_tenant_auth_switch(session, current_user, tid):
    # check that the user can really access the tenant requested

    # grant users of the root tenant access to every tenant
    if current_user.tid == 1:
        return True

    ut = session.query(UserTenant).filter(UserTenant.user_id == current_user.user_id,
                                          UserTenant.tenant_id == tid).one()

    return ut is not None


class AuthenticationHandler(BaseHandler):
    """
    Login handler for admins and recipents and custodians
    """
    check_roles = 'unauthenticated'
    uniform_answer_time = True

    @inlineCallbacks
    def post(self):
        request = self.validate_message(self.request.content.read(), requests.AuthDesc)

        delay = random_login_delay()
        if delay:
            yield deferred_sleep(delay)

        tid = int(request['tid'])
        if tid == 0:
            tid = self.request.tid

        session = yield login(tid,
                              request['username'],
                              request['password'],
                              request['authcode'],
                              self.request.client_using_tor,
                              self.request.client_ip)

        log.debug("Login: Success (%s)" % session.user_role)

        if tid != self.request.tid:
            returnValue({
                'redirect': 'https://%s/#/login?token=%s' % (State.tenant_cache[tid].hostname, session.id)
            })

        returnValue(session.serialize())


class TokenAuthHandler(BaseHandler):
    """
    Login handler for token based authentication
    """
    check_roles = 'unauthenticated'
    uniform_answer_time = True

    @inlineCallbacks
    def post(self):
        request = self.validate_message(self.request.content.read(), requests.TokenAuthDesc)

        tid = int(request['tid'])
        if tid == 0:
            tid = self.request.tid

        delay = random_login_delay()
        if delay:
            yield deferred_sleep(delay)

        session = Sessions.get(request['token'])
        if session is None or session.tid != tid:
            Settings.failed_login_attempts += 1
            raise errors.InvalidAuthentication

        connection_check(self.request.client_ip, tid, session.user_role, self.request.client_using_tor)

        session = Sessions.regenerate(session.id)

        log.debug("Login: Success (%s)" % session.user_role)

        if tid != self.request.tid:
            returnValue({
                'redirect': 'https://%s/#/login?token=%s' % (State.tenant_cache[tid].hostname, session.id)
            })

        returnValue(session.serialize())


class ReceiptAuthHandler(BaseHandler):
    """
    Receipt handler used by whistleblowers
    """
    check_roles = 'unauthenticated'
    uniform_answer_time = True

    @inlineCallbacks
    def post(self):
        request = self.validate_message(self.request.content.read(), requests.ReceiptAuthDesc)

        connection_check(self.request.client_ip, self.request.tid, 'whistleblower', self.request.client_using_tor)

        delay = random_login_delay()
        if delay:
            yield deferred_sleep(delay)

        session = yield login_whistleblower(self.request.tid, request['receipt'])

        log.debug("Login: Success (%s)" % session.user_role)

        returnValue(session.serialize())


class SessionHandler(BaseHandler):
    """
    Session handler for authenticated users
    """
    check_roles = {'admin', 'receiver', 'custodian', 'whistleblower'}

    def get(self):
        """
        Refresh and retrive session
        """
        return self.current_user.serialize()

    def delete(self):
        """
        Logout
        """
        del Sessions[self.current_user.id]


class TenantAuthSwitchHandler(BaseHandler):
    """
    Login handler for switching tenant
    """
    check_roles = {'admin', 'receiver', 'custodian'}

    @inlineCallbacks
    def get(self, tid):
        tid = int(tid)
        check = yield check_tenant_auth_switch(self.current_user, tid)
        if check:
            session = Sessions.new(tid, self.current_user.user_id, self.current_user.user_role, self.current_user.pcn, self.current_user.cc)

        returnValue({
            'redirect': '/t/%d/#/login?token=%s' % (tid, session.id)
        })
