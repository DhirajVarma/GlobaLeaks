# -*- coding: utf-8 -*-
# Implements configuration of Tor hidden service
import os

from distutils.version import LooseVersion  # pylint: disable=no-name-in-module,import-error

from txtorcon import build_local_tor_connection
from twisted.internet import reactor, defer

from globaleaks import models
from globaleaks.db import refresh_memory_variables
from globaleaks.models.config import ConfigFactory
from globaleaks.orm import transact
from globaleaks.rest.cache import Cache
from globaleaks.services.service import Service
from globaleaks.state import State
from globaleaks.utils.utility import deferred_sleep
from globaleaks.utils.log import log

from globaleaks.mocks.txtorcon_mocks import EphemeralHiddenService


__all__ = ['OnionService']


def db_get_onion_service_info(session, tid):
    node = ConfigFactory(session, tid)
    hostname = node.get_val(u'onionservice')
    key = node.get_val(u'tor_onion_key')

    return tid, hostname, key


@transact
def get_onion_service_info(session, tid):
    return db_get_onion_service_info(session, tid)


@transact
def set_onion_service_info(session, tid, hostname, key):
    node = ConfigFactory(session, tid)
    node.set_val(u'onionservice', hostname)
    node.set_val(u'tor_onion_key', key)


@transact
def list_onion_service_info(session):
    return [db_get_onion_service_info(session, x[0])
        for x in session.query(models.Tenant.id).filter(models.Tenant.active == True,
                                                        models.Tenant.id == models.Config.tid,
                                                        models.Config.var_name == u'tor',
                                                        models.Config.value == True)]


class OnionService(Service):
    onion_service_version = 3
    print_startup_error = True
    tor_conn = None
    hs_map = {}
    startup_semaphore = {}

    def reset(self):
        self.tor_con = None
        self.hs_map.clear()
        self.startup_semaphore.clear()

    def stop(self):
        super(OnionService, self).stop()

        if self.tor_conn is None:
            return

        tor_conn = self.tor_conn
        self.tor_conn = None
        return tor_conn.protocol.quit()

    @defer.inlineCallbacks
    def add_all_hidden_services(self):
        if self.tor_conn is None:
            return

        hostname_key_list = yield list_onion_service_info()
        for tid, hostname, key in hostname_key_list:
            if hostname not in self.hs_map:
                yield self.add_hidden_service(tid, hostname, key)

    def add_hidden_service(self, tid, hostname, key):
        if self.tor_conn is None:
            return

        hs_loc = ('80 localhost:8083')
        if not hostname and not key:
            if tid in self.startup_semaphore:
                log.debug('Still waiting for hidden service to start', tid=tid)
                return self.startup_semaphore[tid]

            log.info('Creating new onion service', tid=tid)

            if self.onion_service_version == 3:
                ephs = EphemeralHiddenService(hs_loc, 'NEW:ED25519-V3')
            else:
                ephs = EphemeralHiddenService(hs_loc, 'NEW:RSA1024')
        else:
            log.info('Setting up existing onion service %s', hostname, tid=tid)
            ephs = EphemeralHiddenService(hs_loc, key)
            self.hs_map[hostname] = ephs

        @defer.inlineCallbacks
        def init_callback(ret):
            log.info('Initialization of hidden-service %s completed.', ephs.hostname, tid=tid)
            if not hostname and not key:
                if tid in State.tenant_cache:
                    self.hs_map[ephs.hostname] = ephs
                    yield set_onion_service_info(tid, ephs.hostname, ephs.private_key)
                else:
                    yield ephs.remove_from_tor(self.tor_conn.protocol)

                tid_list = list(set([1, tid]))

                for x in tid_list:
                    Cache().invalidate(x)

                yield refresh_memory_variables(tid_list)

                del self.startup_semaphore[tid]

        def init_errback(failure):
            if tid in self.startup_semaphore:
                del self.startup_semaphore[tid]

            raise failure.value

        self.startup_semaphore[tid] = ephs.add_to_tor(self.tor_conn.protocol)

        return self.startup_semaphore[tid].addCallbacks(init_callback, init_errback)  # pylint: disable=no-member

    @defer.inlineCallbacks
    def remove_unwanted_hidden_services(self):
        # Collect the list of all hidden services listed by tor then remove all of them
        # that are not present in the tenant cache ensuring that OnionService.hs_map is
        # kept up to date.
        running_services = yield self.get_all_hidden_services()

        tenant_services = {State.tenant_cache[tid].onionservice for tid in State.tenant_cache}

        for onion_addr in running_services:
            ephs = None
            if onion_addr not in tenant_services and onion_addr in self.hs_map:
                ephs = self.hs_map.pop(onion_addr)

            if ephs is not None:
                log.info('Removing onion address %s', ephs.hostname)
                yield ephs.remove_from_tor(self.tor_conn.protocol)

    @defer.inlineCallbacks
    def get_all_hidden_services(self):
        if self.tor_conn is None:
            defer.returnValue([])

        ret = yield self.tor_conn.protocol.get_info('onions/current')
        if ret == '':
            running_services = []
        else:
            x = ret.get('onions/current', '').strip().split('\n')
            running_services = [r+'.onion' for r in x]

        defer.returnValue(running_services)

    def operation(self):
        restart_deferred = defer.Deferred()

        control_socket = '/var/run/tor/control'

        self.reset()

        @defer.inlineCallbacks
        def startup_callback(tor_conn):
            self.print_startup_error = True
            self.tor_conn = tor_conn
            self.tor_conn.protocol.on_disconnect = restart_deferred

            log.err('Successfully connected to Tor control port')

            try:
                version = yield self.tor_conn.protocol.queue_command("GETINFO version")
                version = version.split('=')[1]
                if LooseVersion(version) < LooseVersion('0.3.3.9'):
                    self.onion_service_version = 2
            except:
                pass

            yield self.add_all_hidden_services()

        def startup_errback(err):
            if self.print_startup_error:
                # Print error only on first run or failure or on a failure subsequent to a success condition
                self.print_startup_error = False
                log.err('Failed to initialize Tor connection; error: %s', err)

            restart_deferred.callback(None)

        if not os.path.exists(control_socket):
            startup_errback(Exception('Tor control port not open on %s; waiting for Tor to become available' % control_socket))
            return deferred_sleep(1)

        if not os.access(control_socket, os.R_OK):
            startup_errback(Exception('Unable to access %s; manual permission recheck needed' % control_socket))
            return deferred_sleep(1)

        build_local_tor_connection(reactor).addCallbacks(startup_callback, startup_errback)

        return restart_deferred
