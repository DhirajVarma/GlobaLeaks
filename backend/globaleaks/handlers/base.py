# -*- coding: utf-8 -*-
#
# Base class for all the handlers
import base64
import collections
import json
import mimetypes
import os
import re

from datetime import datetime
from cryptography.hazmat.primitives import constant_time
from six import text_type, binary_type
from twisted.internet import abstract, defer
from twisted.protocols.basic import FileSender

from globaleaks.event import track_handler
from globaleaks.rest import errors, requests
from globaleaks.utils.crypto import sha512
from globaleaks.utils.securetempfile import SecureTemporaryFile
from globaleaks.sessions import Sessions
from globaleaks.settings import Settings
from globaleaks.utils.log import log
from globaleaks.utils.utility import datetime_now, deferred_sleep

# https://github.com/globaleaks/GlobaLeaks/issues/1601
mimetypes.add_type('image/svg+xml', '.svg')
mimetypes.add_type('application/vnd.ms-fontobject', '.eot')
mimetypes.add_type('application/x-font-ttf', '.ttf')
mimetypes.add_type('application/woff', '.woff')
mimetypes.add_type('application/woff2', '.woff2')


def serve_file(request, fo):
    def on_finish(ignored):
        fo.close()
        request.finish()

    filesender = FileSender().beginFileTransfer(fo, request)

    filesender.addBoth(on_finish)

    return filesender


class BaseHandler(object):
    check_roles = 'admin'
    handler_exec_time_threshold = 120
    uniform_answer_time = False
    cache_resource = False
    invalidate_cache = False
    bypass_basic_auth = False
    root_tenant_only = False
    upload_handler = False
    uploaded_file = None
    require_multisite = False
    refresh_connection_handpoints = False

    def __init__(self, state, request):
        self.name = type(self).__name__
        self.state = state
        self.request = request
        self.request.start_time = datetime.now()

    def basic_auth(self):
        msg = None
        if b"authorization" in self.request.headers:
            try:
                auth_type, data = self.request.headers[b"authorization"].split()
                usr, pwd = text_type(base64.b64decode(data), 'utf-8').split(":", 1)
                if auth_type != b"Basic" or \
                    usr != self.state.tenant_cache[self.request.tid].basic_auth_username or \
                    pwd != self.state.tenant_cache[self.request.tid].basic_auth_password:
                    msg = "Authentication failed"
            except AssertionError:
                msg = "Authentication failed"
        else:
            msg = "Authentication required"

        if msg is not None:
            self.request.setHeader(b'WWW-Authenticate', b'Basic realm="globaleaks"')
            raise errors.HTTPAuthenticationRequired()

    @staticmethod
    def validate_python_type(value, python_type):
        """
        Return True if the python class instantiates the specified python_type.
        """
        if value is None:
            return True

        if python_type == requests.SkipSpecificValidation:
            return True

        if python_type == int:
            try:
                int(value)
                return True
            except:
                return False

        if python_type == bool:
            if value == u'true' or value == u'false':
                return True

        return isinstance(value, python_type)

    @staticmethod
    def validate_regexp(value, type):
        """
        Return True if the python class matches the given regexp.
        """
        try:
            value = text_type(value)
        except:
            return False

        return bool(re.match(type, value))

    @staticmethod
    def validate_type(value, type):
        retval = False

        if value is None:
            log.err("-- Invalid python_type, in [%s] expected %s", value, type)

        # if it's callable, than assumes is a primitive class
        elif callable(type):
            retval = BaseHandler.validate_python_type(value, type)
            if not retval:
                log.err("-- Invalid python_type, in [%s] expected %s", value, type)

        # value as "{foo:bar}"
        elif isinstance(type, collections.Mapping):
            retval = BaseHandler.validate_jmessage(value, type)
            if not retval:
                log.err("-- Invalid JSON/dict [%s] expected %s", value, type)

        # regexp
        elif isinstance(type, str):
            retval = BaseHandler.validate_regexp(value, type)
            if not retval:
                log.err("-- Failed Match in regexp [%s] against %s", value, type)

        # value as "[ type ]"
        elif isinstance(type, collections.Iterable):
            # empty list is ok
            if not value:
                retval = True

            else:
                retval = all(BaseHandler.validate_type(x, type[0]) for x in value)
                if not retval:
                    log.err("-- List validation failed [%s] of %s", value, type)

        return retval

    @staticmethod
    def validate_jmessage(jmessage, message_template):
        """
        Takes a string that represents a JSON messages and checks to see if it
        conforms to the message type it is supposed to be.

        This message must be either a dict or a list. This function may be called
        recursively to validate sub-parameters that are also go GLType.

        message: the message string that should be validated

        message_type: the GLType class it should match.
        """
        if isinstance(message_template, dict):
            success_check = 0
            keys_to_strip = []
            for key, value in jmessage.items():
                if key not in message_template:
                    # strip whatever is not validated
                    #
                    # reminder: it's not possible to raise an exception for the
                    # in case more values are present because it's normal that the
                    # client will send automatically more data.
                    #
                    # e.g. the client will always send 'creation_date' attributes of
                    #      objects and attributes like this are present generally only
                    #      from the second request on.
                    #
                    keys_to_strip.append(key)
                    continue

                if not BaseHandler.validate_type(value, message_template[key]):
                    log.err("Received key %s: type validation fail", key)
                    raise errors.InputValidationError("Key (%s) type validation failure" % key)
                success_check += 1

            for key in keys_to_strip:
                del jmessage[key]

            for key, value in message_template.items():
                if key not in jmessage:
                    log.debug("Key %s expected but missing!", key)
                    log.debug("Received schema %s - Expected %s",
                              jmessage.keys(), message_template.keys())
                    raise errors.InputValidationError("Missing key %s" % key)

                if not BaseHandler.validate_type(jmessage[key], value):
                    log.err("Expected key: %s type validation failure", key)
                    raise errors.InputValidationError("Key (%s) double validation failure" % key)

                if isinstance(message_template[key], (dict, list)) and message_template[key]:
                    BaseHandler.validate_jmessage(jmessage[key], message_template[key])

                success_check += 1

            if success_check != len(message_template) * 2:
                log.err("Success counter double check failure: %d", success_check)
                raise errors.InputValidationError("Success counter double check failure")

            return True

        elif isinstance(message_template, list):
            if not all(BaseHandler.validate_type(x, message_template[0]) for x in jmessage):
                raise errors.InputValidationError("Not every element in %s is %s" %
                                                (jmessage, message_template[0]))
            return True

        else:
            raise errors.InputValidationError("invalid json massage: expected dict or list")

    @staticmethod
    def validate_message(message, message_template):
        try:
            if isinstance(message, binary_type):
                message = message.decode('utf-8')

            jmessage = json.loads(message)
        except ValueError:
            raise errors.InputValidationError("Invalid JSON format")

        if BaseHandler.validate_jmessage(jmessage, message_template):
            return jmessage

        raise errors.InputValidationError("Unexpected condition!?")

    def redirect(self, url):
        self.request.setResponseCode(301)
        self.request.setHeader(b'location', url)

    def check_file_presence(self, filepath):
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            raise errors.ResourceNotFound()

    def open_file(self, filepath):
        self.check_file_presence(filepath)

        return open(filepath, 'rb')

    def write_file_fo(self, filename, fo):
        if filename.endswith('.gz'):
            self.request.setHeader(b'Content-encoding', b'gzip')
            filename = filename[:-3]

        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type:
            self.request.setHeader(b'Content-Type', mime_type)

        return serve_file(self.request, fo)

    def write_file(self, filename, filepath):
        fo = self.open_file(filepath)
        return self.write_file_fo(filename, fo)

    def write_file_as_download_fo(self, filename, fo):
        self.request.setHeader(b'X-Download-Options', b'noopen')
        self.request.setHeader(b'Content-Type', b'application/octet-stream')
        self.request.setHeader(b'Content-Disposition', 'attachment; filename="%s"' % filename)

        return serve_file(self.request, fo)

    def write_file_as_download(self, filename, filepath):
        fo = self.open_file(filepath)
        return self.write_file_as_download_fo(filename, fo)

    def get_current_user(self):
        api_session = self.get_api_session()
        if api_session is not None:
            return api_session

        # Check for the session header
        session_id = self.request.headers.get(b'x-session')
        if session_id is None:
            return

        session = Sessions.get(text_type(session_id, 'utf-8'))

        if session is not None and session.tid == self.request.tid:
            self.request.current_user = session

            if self.request.current_user.user_role != 'whistleblower' and \
               self.state.tenant_cache[1].get(u'log_accesses_of_internal_users', False):
                self.request.log_ip_and_ua = True

        return session

    @property
    def current_user(self):
        if not hasattr(self, '_current_user'):
            self._current_user = self.get_current_user()

        return self._current_user

    def get_api_session(self):
        token = ''
        if b'api-token' in self.request.args:
            token = binary_type(self.request.args[b'api-token'][0])
        elif b'x-api-token' in self.request.headers:
            token = binary_type(self.request.headers[b'x-api-token'])

        # Assert the input is okay and the api_token state is acceptable
        if self.request.tid != 1 or \
           self.state.api_token_session is None or \
           not self.state.tenant_cache[self.request.tid].admin_api_token_digest:
            return

        stored_token_hash = self.state.tenant_cache[self.request.tid].admin_api_token_digest.encode()

        if constant_time.bytes_eq(sha512(token), stored_token_hash):
            return self.state.api_token_session

    def process_file_upload(self):
        if b'flowFilename' not in self.request.args:
            return

        total_file_size = int(self.request.args[b'flowTotalSize'][0])
        flow_identifier = self.request.args[b'flowIdentifier'][0]

        chunk_size = len(self.request.args[b'file'][0])
        if ((chunk_size / (1024 * 1024)) > self.state.tenant_cache[self.request.tid].maximum_filesize or
            (total_file_size / (1024 * 1024)) > self.state.tenant_cache[self.request.tid].maximum_filesize):
            log.err("File upload request rejected: file too big", tid=self.request.tid)
            raise errors.FileTooBig(self.state.tenant_cache[self.request.tid].maximum_filesize)

        if flow_identifier not in self.state.TempUploadFiles:
            self.state.TempUploadFiles.set(flow_identifier, SecureTemporaryFile(Settings.tmp_path))

        f = self.state.TempUploadFiles[flow_identifier]
        with f.open('w') as f:
            f.write(self.request.args[b'file'][0])

            if self.request.args[b'flowChunkNumber'][0] != self.request.args[b'flowTotalChunks'][0]:
                return None

            f.finalize_write()

        mime_type, _ = mimetypes.guess_type(text_type(self.request.args[b'flowFilename'][0], 'utf-8'))
        if mime_type is None:
            mime_type = 'application/octet-stream'

        filename = self.request.args[b'flowFilename'][0].decode('utf-8')

        self.uploaded_file = {
            'date': datetime_now(),
            'name': filename,
            'type': mime_type,
            'size': total_file_size,
            'filename': os.path.basename(f.filepath),
            'body': f,
            'description': self.request.args.get(b'description', [''])[0]
        }

    def write_upload_plaintext_to_disk(self, destination):
        """
        @param uploaded_file: uploaded_file data struct
        @param the file destination
        @return: a descriptor dictionary for the saved file
        """
        try:
            log.debug('Creating file %s with %d bytes', destination, self.uploaded_file['size'])

            with self.uploaded_file['body'].open('r') as encrypted_file, open(destination, 'wb') as plaintext_file:
                while True:
                    chunk = encrypted_file.read(abstract.FileDescriptor.bufferSize)
                    if not chunk:
                        break

                    plaintext_file.write(chunk)

        finally:
            self.uploaded_file['path'] = destination

    def execution_check(self):
        self.request.execution_time = datetime.now() - self.request.start_time

        if self.request.execution_time.seconds > self.handler_exec_time_threshold:
            err_tup = ("Handler [%s] exceeded execution threshold (of %d secs) with an execution time of %.2f seconds",
                       self.name, self.handler_exec_time_threshold, self.request.execution_time.seconds)
            log.err(tid=self.request.tid, *err_tup)
            self.state.schedule_exception_email(*err_tup)

        track_handler(self)

        if self.uniform_answer_time:
            needed_delay = (Settings.side_channels_guard - (self.request.execution_time.microseconds / 1000)) / 1000
            if needed_delay > 0:
                return deferred_sleep(needed_delay)
