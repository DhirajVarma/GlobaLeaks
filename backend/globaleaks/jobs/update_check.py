# -*- coding: utf-8 -*-
from debian import deb822
from distutils.version import LooseVersion as V  # pylint: disable=no-name-in-module,import-error
from six import text_type
from twisted.internet.defer import inlineCallbacks

from globaleaks import __version__
from globaleaks.handlers.admin.node import db_admin_serialize_node
from globaleaks.handlers.admin.notification import db_get_notification
from globaleaks.handlers.admin.user import db_get_admin_users
from globaleaks.jobs.job import HourlyJob
from globaleaks.models.config import ConfigFactory
from globaleaks.orm import transact
from globaleaks.rest.cache import Cache
from globaleaks.utils.agent import get_page
from globaleaks.utils.log import log

DEB_PACKAGE_URL = b'https://deb.globaleaks.org/bionic/Packages'


@transact
def evaluate_update_notification(session, state, latest_version):
    priv_fact = ConfigFactory(session, 1)

    stored_latest = priv_fact.get_val(u'latest_version')

    if V(stored_latest) < V(latest_version):
        Cache.invalidate()

        priv_fact.set_val(u'latest_version', latest_version)

        if V(__version__) == V(latest_version):
            return

        for user_desc in db_get_admin_users(session, 1):
            lang = user_desc['language']
            template_vars = {
                'type': 'software_update_available',
                'latest_version': latest_version,
                'node': db_admin_serialize_node(session, 1, lang),
                'notification': db_get_notification(session, 1, lang),
                'user': user_desc,
            }

            state.format_and_send_mail(session, 1, user_desc, template_vars)


class UpdateCheck(HourlyJob):
    interval = 60*60*24

    def fetch_packages_file(self):
        return get_page(self.state.get_agent(), DEB_PACKAGE_URL)

    @inlineCallbacks
    def operation(self):
        log.debug('Fetching latest GlobaLeaks version from repository')
        packages_file = yield self.fetch_packages_file()
        packages_file = text_type(packages_file, 'utf-8')
        versions = [p['Version'] for p in deb822.Deb822.iter_paragraphs(packages_file) if p['Package'] == 'globaleaks']
        versions.sort(key=V)

        latest_version = versions[-1]

        yield evaluate_update_notification(self.state, latest_version)

        log.debug('The newest version in the repository is: %s', latest_version)
