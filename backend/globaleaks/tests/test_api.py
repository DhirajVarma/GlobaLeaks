# -*- coding: utf-8 -*-
from twisted.internet.address import IPv4Address
from twisted.internet.defer import inlineCallbacks

from globaleaks.db import refresh_memory_variables
from globaleaks.handlers.admin.node import db_update_enabled_languages
from globaleaks.orm import tw
from globaleaks.state import State
from globaleaks.tests.helpers import TestGL, forge_request


class TestAPI(TestGL):
    @inlineCallbacks
    def setUp(self):
        yield TestGL.setUp(self)

        from globaleaks.rest import api
        self.api = api.APIResourceWrapper()

        yield tw(db_update_enabled_languages, 1, ['en', 'ar', 'it'], 'en')
        yield refresh_memory_variables()

    def test_api_spec(self):
        from globaleaks.rest import api
        for spec in api.api_spec:
            check_roles = getattr(spec[1], 'check_roles')
            self.assertIsNotNone(check_roles)

            if isinstance(check_roles, str):
                check_roles = {check_roles}

            self.assertTrue(len(check_roles) >= 1)
            self.assertTrue('*' not in check_roles or len(check_roles) == 1)
            self.assertTrue('unauthenticated' not in check_roles or len(check_roles) == 1)
            self.assertTrue('*' not in check_roles or len(check_roles) == 1)

            rest = list(filter(lambda a: a not in ['*',
                                              'unauthenticated',
                                              'whistleblower',
                                              'admin',
                                              'receiver',
                                              'custodian'], check_roles))
            self.assertTrue(len(rest) == 0)

    def test_get_with_no_language_header(self):
        request = forge_request()
        self.assertEqual(self.api.detect_language(request), 'en')

    def test_get_with_gl_language_header(self):
        request = forge_request(headers={'GL-Language': 'it'})
        self.assertEqual(self.api.detect_language(request), 'it')

    def test_get_with_accept_language_header(self):
        request = forge_request(headers={'Accept-Language': 'ar;q=0.8,it;q=0.6'})
        self.assertEqual(self.api.detect_language(request), 'ar')

    def test_get_with_gl_language_header_and_accept_language_header_1(self):
        request = forge_request(headers={'GL-Language': 'en',
                                'Accept-Language': 'en-US,en;q=0.8,it;q=0.6'})
        self.assertEqual(self.api.detect_language(request), 'en')

    def test_get_with_gl_language_header_and_accept_language_header_2(self):
        request = forge_request(headers={'GL-Language': 'antani',
                                'Accept-Language': 'en-US,en;it;q=0.6'})
        self.assertEqual(self.api.detect_language(request), 'en')

    def test_get_with_gl_language_header_and_accept_language_header_3(self):
        request = forge_request(headers={'GL-Language': 'antani',
                                'Accept-Language': 'antani1,antani2;q=0.8,antani3;q=0.6'})
        self.assertEqual(self.api.detect_language(request), 'en')

    def test_status_codes_assigned(self):
        test_cases = [
            (b'GET', 200),
            (b'HEAD', 501),
            (b'POST', 501),
            (b'PUT', 501),
            (b'DELETE', 501),
            (b'XXX', 501),
            (b'', 501),
        ]

        server_headers = [
           ('X-Content-Type-Options', 'nosniff'),
           ('Expires', '-1'),
           ('Server', 'Globaleaks'),
           ('Pragma', 'no-cache'),
           ('Cache-control', 'no-cache, no-store, must-revalidate'),
           ('Referrer-Policy', 'no-referrer'),
           ('X-Frame-Options', 'deny')
        ]

        for meth, status_code in test_cases:
            request = forge_request(uri=b"https://www.globaleaks.org/", method=meth)
            self.api.render(request)
            self.assertEqual(request.responseCode, status_code)
            for headerName, expectedHeaderValue in server_headers:
                returnedHeaderValue = request.responseHeaders.getRawHeaders(headerName)[0]
                self.assertEqual(returnedHeaderValue, expectedHeaderValue)

    def test_request_state(self):
        url = b"https://www.globaleaks.org/"

        request = forge_request(url)
        self.api.render(request)
        self.assertFalse(request.client_using_tor)
        self.assertEqual(request.responseCode, 200)

        request = forge_request(url, client_addr=IPv4Address('TCP', '127.0.0.1', 12345))
        self.api.render(request)
        self.assertFalse(request.client_using_tor)
        self.assertEqual(request.responseCode, 200)

        request = forge_request(uri=b'http://127.0.0.1:8083/', client_addr=IPv4Address('TCP', '127.0.0.1', 12345))
        self.api.render(request)
        self.assertTrue(request.client_using_tor)
        self.assertEqual(request.responseCode, 200)
