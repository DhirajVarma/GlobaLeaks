# -*- coding: utf-8 -*-
#
# This filte contains routines dealing with texts templates and variables replacement used
# mainly in mail notifications.
import collections
import copy

from datetime import timedelta

from six import text_type

from globaleaks import __version__
from globaleaks.rest import errors
from globaleaks.utils.utility import datetime_to_pretty_str, \
    ISO8601_to_datetime, ISO8601_to_pretty_str, ISO8601_to_day_str, \
    bytes_to_pretty_str

node_keywords = [
    '{NodeName}',
    '{TorSite}',
    '{HTTPSSite}',
    '{TorUrl}',
    '{HTTPSUrl}',
    '{Site}',
    '{Url}',
    '{DocumentationUrl}',
    '{LoginUrl}',
]

context_keywords = [
    '{ContextName}'
]

user_keywords = [
    '{RecipientName}'
]

tip_keywords = [
    '{TipID}',
    '{TipNum}',
    '{TipLabel}',
    '{EventTime}',
    '{SubmissionDate}',
    '{QuestionnaireAnswers}',
    '{Comments}',
    '{Messages}'
]

file_keywords = [
    '{FileName}',
    '{FileSize}'
]

export_message_keywords = [
    '{Content}'
]

expiration_summary_keywords = [
    '{ExpiringSubmissionCount}',
    '{EarliestExpirationDate}'
]

admin_pgp_alert_keywords = [
    '{PGPKeyInfoList}'
]

user_pgp_alert_keywords = [
    '{PGPKeyInfo}'
]

admin_anomaly_keywords = [
    '{AnomalyDetailDisk}',
    '{AnomalyDetailActivities}',
    '{ActivityAlarmLevel}',
    '{ActivityDump}',
    '{FreeMemory}',
    '{TotalMemory}'
]

https_expr_keywords = [
    '{ExpirationDate}'
]

software_update_keywords = [
    '{InstalledVersion}',
    '{LatestVersion}',
    '{ChangeLogUrl}',
    '{UpdateGuideUrl}',
]

user_credentials_keywords = [
    '{Role}',
    '{Username}',
    '{Password}'
]

platform_signup_keywords = [
    '{RecipientName}',
    '{ActivationUrl}',
    '{ExpirationDate}',
    '{Name}',
    '{Surname}',
    '{Email}',
    '{UseCase}',
    '{Language}',
    '{AdminCredentials}',
    '{RecipientCredentials}'
]

email_validation_keywords = [
    '{RecipientName}',
    '{NewEmailAddress}'
]

password_reset_validation_keywords = [
    '{RecipientName}'
]

password_reset_complete_keywords = [
    '{RecipientName}',
    '{NewPassword}'
]

identity_access_request_keywords = [
    '{RecipientName}',
    '{TipNum}',
]

two_factor_auth_keywords = [
    '{AuthCode}'
]


def indent(n=1):
    return '  ' * n


def indent_text(text, n=1):
    """
    Add n * 2 space as indentation to each of the non empty lines of the provided text
    """
    return '\n'.join([('  ' * n if not l.isspace() else '') + l for l in text.splitlines()])


class Keyword(object):
    keyword_list = []
    data_keys = []

    def __init__(self, data):
        for k in self.data_keys:
            if k not in data:
                raise errors.InternalServerError('Missing key \'%s\' while resolving template \'%s\'' % (k, type(self).__name__))

        self.data = data


class NodeKeyword(Keyword):
    keyword_list = node_keywords
    data_keys = ['node', 'notification']

    def NodeName(self):
        return self.data['node']['name']

    def TorSite(self):
        if self.data['node']['onionservice']:
            return 'http://' + self.data['node']['onionservice']

        return '[UNDEFINED]'

    def HTTPSSite(self):
        if self.data['node']['hostname']:
            return 'https://' + self.data['node']['hostname']

        return '[UNDEFINED]'

    def Site(self):
        if self.data['node']['hostname']:
            return self.HTTPSSite()

        elif self.data['node']['onionservice']:
            return self.TorSite()

        return ''

    def UrlPath(self):
        return '/'

    def Url(self):
        return self.Site() + self.UrlPath()

    def TorUrl(self):
        return self.TorSite() + self.UrlPath()

    def HTTPSUrl(self):
        return self.HTTPSSite() + self.UrlPath()

    def DocumentationUrl(self):
        return 'https://docs.globaleaks.org'

    def LoginUrl(self):
        return self.Site() + '/#/login'


class UserKeyword(Keyword):
    keyword_list = user_keywords
    data_keys = ['user']

    def RecipientName(self):
        return self.data['user']['name']


class UserNodeKeyword(NodeKeyword, UserKeyword):
    keyword_list = NodeKeyword.keyword_list + UserKeyword.keyword_list
    data_keys = NodeKeyword.data_keys + UserKeyword.data_keys


class ContextKeyword(Keyword):
    keyword_list = context_keywords
    data_keys = ['context']

    def ContextName(self):
        return self.data['context']['name']


class TipKeyword(UserNodeKeyword, ContextKeyword):
    keyword_list = UserNodeKeyword.keyword_list + ContextKeyword.keyword_list + tip_keywords
    data_keys =  UserNodeKeyword.data_keys + ContextKeyword.data_keys + ['tip']

    def dump_field_entry(self, output, field, entry, indent_n):

        field_type = field['type']

        if field_type == 'checkbox':
            for k, v in entry.items():
                for option in field['options']:
                    if k == option.get('id', '') and v == True:
                        output += indent(indent_n) + option['label'] + '\n'
        elif field_type in ['selectbox']:
            for option in field['options']:
                if entry.get('value', '') == option['id']:
                    output += indent(indent_n) + option['label'] + '\n'
        elif field_type == 'date':
            date = entry.get('value')
            if date is not None:
                output += indent(indent_n) + ISO8601_to_pretty_str(entry.get('value')) + '\n'
        elif field_type == 'tos':
            answer = '☑' if entry.get('value', '') == True else '☐'
            output += indent(indent_n) + answer + '\n'
        elif field_type == 'fieldgroup':
            output = self.dump_fields(output, field['children'], entry, indent_n)
        else:
            output += indent_text(entry.get('value', ''), indent_n) + '\n'

        return output + '\n'

    def dump_fields(self, output, fields, answers, indent_n):
        rows = {}
        for f in fields:
            y = f['y']
            if y not in rows:
                rows[y] = []
            rows[y].append(f)

        rows = collections.OrderedDict(sorted(rows.items()))

        for r in rows:
            rows[r] = sorted(rows[r], key=lambda k: k['x'])

        for _, row in rows.items():
            for field in row:
                if field['id'] not in answers or \
                   field['type'] == 'fileupload' or \
                   field['template_id'] ==  'whistleblower_identity':
                    continue

                if field['id'] in answers:
                    output += indent(indent_n) + field['label'] + '\n'
                    entries = answers[field['id']]
                    if len(entries) == 1:
                        output = self.dump_field_entry(output, field, entries[0], indent_n + 1)
                    else:
                        i = 1
                        for entry in entries:
                            output += indent(indent_n) + '#' + str(i) + '\n'
                            output = self.dump_field_entry(output, field, entry, indent_n + 2)
                            i += 1

        return output

    def dump_questionnaire_answers(self, questionnaire, answers):
        output = ''

        questionnaire = sorted(questionnaire, key=lambda k: k['presentation_order'])

        for step in questionnaire:
            output += step['label'] + '\n'
            output = self.dump_fields(output, step['children'], answers, 1) +'\n'

        return output

    def dump_messages(self, messages):
        ret = ''
        for message in messages:
            data = copy.deepcopy(self.data)
            data['type'] = 'export_message'
            data['message'] = copy.deepcopy(message)
            template = 'export_message_whistleblower' if (message['type'] == 'whistleblower') else 'export_message_recipient'
            ret += indent_text('-' * 40) + '\n'
            ret += indent_text(text_type(Templating().format_template(self.data['notification'][template], data))) + '\n\n'

        return ret

    def TipID(self):
        return self.data['tip']['id']

    def UrlPath(self):
        return '/#/status/' + self.data['tip']['id']

    def TipNum(self):
        return str(self.data['tip']['progressive'])

    def TipLabel(self):
        return self.data['tip']['label']

    def EventTime(self):
        return ISO8601_to_pretty_str(self.data['tip']['creation_date'])

    def SubmissionDate(self):
        return self.EventTime()

    def QuestionnaireAnswers(self):
        return self.dump_questionnaire_answers(self.data['tip']['questionnaires'][0]['steps'], self.data['tip']['questionnaires'][0]['answers'])

    def Comments(self):
        comments = self.data.get('comments', [])
        if not len(comments):
            return '{Blank}'

        ret = 'Comments:\n'
        ret += self.dump_messages(comments) + '\n'
        return ret + '\n'

    def Messages(self):
        messages = self.data.get('messages', [])
        if not len(messages):
            return '{Blank}'

        ret = 'Private messages:\n'
        ret += self.dump_messages(messages)
        return ret + '\n'


class CommentKeyword(TipKeyword):
    data_keys =  TipKeyword.data_keys + ['comment']

    def EventTime(self):
        return ISO8601_to_pretty_str(self.data['comment']['creation_date'])


class MessageKeyword(TipKeyword):
    data_keys =  TipKeyword.data_keys + ['message']

    def EventTime(self):
        return ISO8601_to_pretty_str(self.data['message']['creation_date'])


class FileKeyword(TipKeyword):
    keyword_list = TipKeyword.keyword_list + file_keywords
    data_keys =  TipKeyword.data_keys + ['file']

    def FileName(self):
        return self.data['file']['name']

    def EventTime(self):
        return ISO8601_to_pretty_str(self.data['file']['creation_date'])

    def FileSize(self):
        return str(self.data['file']['size'])


class ExportMessageKeyword(TipKeyword):
    keyword_list = TipKeyword.keyword_list + export_message_keywords
    data_keys =  TipKeyword.data_keys + ['message']

    def Content(self):
        return self.data['message']['content']

    def EventTime(self):
        return ISO8601_to_pretty_str(self.data['message']['creation_date'])


class ExpirationSummaryKeyword(UserNodeKeyword):
    keyword_list = UserNodeKeyword.keyword_list + expiration_summary_keywords
    data_keys =  UserNodeKeyword.data_keys + ['expiring_submission_count', 'earliest_expiration_date']

    def ExpiringSubmissionCount(self):
        return str(self.data['expiring_submission_count'])

    def EarliestExpirationDate(self):
        return ISO8601_to_pretty_str(self.data['earliest_expiration_date'])

    def UrlPath(self):
        return '/#/receiver/tips'


class AdminPGPAlertKeyword(UserNodeKeyword):
    keyword_list = UserNodeKeyword.keyword_list + admin_pgp_alert_keywords
    data_keys =  UserNodeKeyword.data_keys + ['users']

    def PGPKeyInfoList(self):
        ret = ''
        for r in self.data['users']:
            fingerprint = r['pgp_key_fingerprint']
            key = fingerprint[:7] if fingerprint is not None else ''

            ret += '\t%s, %s (%s)\n' % (r['name'],
                                        key,
                                        ISO8601_to_day_str(r['pgp_key_expiration']))
        return ret


class PGPAlertKeyword(UserNodeKeyword):
    keyword_list = UserNodeKeyword.keyword_list + user_pgp_alert_keywords

    def PGPKeyInfo(self):
        fingerprint = self.data['user']['pgp_key_fingerprint']
        key = fingerprint[:7] if fingerprint is not None else ''

        return '\t0x%s (%s)' % (key, ISO8601_to_day_str(self.data['user']['pgp_key_expiration']))


class AnomalyKeyword(UserNodeKeyword):
    keyword_list = UserNodeKeyword.keyword_list + admin_anomaly_keywords
    data_keys =  UserNodeKeyword.data_keys + ['alert']

    def AnomalyDetailDisk(self):
        # This happens all the time anomalies are present but disk is ok
        if self.data['alert']['alarm_levels']['disk_space'] == 0:
            return u''

        if self.data['alert']['alarm_levels']['disk_space'] == 1:
            return self.data['notification']['admin_anomaly_disk_low']
        else:
            return self.data['notification']['admin_anomaly_disk_high']

    def AnomalyDetailActivities(self):
        # This happens all the time there is not anomalous traffic
        if self.data['alert']['alarm_levels']['activity'] == 0:
            return u''

        return self.data['notification']['admin_anomaly_activities']

    def ActivityAlarmLevel(self):
        return '%s' % self.data['alert']['alarm_levels']['activity']

    def ActivityDump(self):
        retstr = ''

        for event, count in self.data['alert']['event_matrix'].items():
            if not count:
                continue
            retstr = '%s%s%d\n%s' % (event, (25 - len(event)) * ' ', count, retstr)

        return retstr

    def FreeMemory(self):
        return '%s' % bytes_to_pretty_str(self.data['alert']['measured_freespace'])

    def TotalMemory(self):
        return '%s' % bytes_to_pretty_str(self.data['alert']['measured_totalspace'])


class CertificateExprKeyword(UserNodeKeyword):
    keyword_list = UserNodeKeyword.keyword_list + https_expr_keywords
    data_keys = UserNodeKeyword.data_keys + ['expiration_date']

    def ExpirationDate(self):
        return ISO8601_to_pretty_str(self.data['expiration_date'])

    def UrlPath(self):
        return '/#/admin/network'


class SoftwareUpdateKeyword(UserNodeKeyword):
    keyword_list = UserNodeKeyword.keyword_list + software_update_keywords
    data_keys = UserNodeKeyword.data_keys + ['latest_version']

    def LatestVersion(self):
        return '%s' % self.data['latest_version']

    def InstalledVersion(self):
        return '%s' % __version__

    def ChangeLogUrl(self):
        return 'https://www.globaleaks.org/r/changelog'

    def UpdateGuideUrl(self):
        return 'https://www.globaleaks.org/r/upgrade-guide'


class UserCredentials(Keyword):
    keyword_list = user_credentials_keywords
    data_keys = ['role', 'username', 'password']

    def Role(self):
        return '%s' % self.data['role']

    def Username(self):
        return '%s' % self.data['username']

    def Password(self):
        return '%s' % self.data['password']


class PlatformSignupKeyword(NodeKeyword):
    keyword_list = NodeKeyword.keyword_list + platform_signup_keywords
    data_keys = NodeKeyword.data_keys + ['signup']

    def TorSite(self):
        return 'http://' + self.data['signup']['subdomain'] + '.' + self.data['node']['onionservice']

    def HTTPSSite(self):
        return 'https://' + self.data['signup']['subdomain'] + '.' + self.data['node']['rootdomain']

    def Site(self):
        if self.data['node']['hostname']:
            return self.HTTPSSite()

        elif self.data['node']['onionservice']:
            return self.TorSite()

        return ''

    def RecipientName(self):
        return self.data['signup']['name'] + ' ' + self.data['signup']['surname']

    def ActivationUrl(self):
        if self.data['node']['hostname']:
            site = 'https://' + self.data['node']['hostname']
        elif self.data['node']['onionservice']:
            site = 'http://' + self.data['node']['onionservice']
        else:
            site = ''

        return site + '/#/activation?token=' + self.data['signup']['activation_token']

    def LoginUrl(self):
        return self.Site() + '/#/login'

    def ExpirationDate(self):
        date = ISO8601_to_datetime(self.data['signup']['registration_date']) + timedelta(days=30)
        return datetime_to_pretty_str(date)

    def Name(self):
        return self.data['signup']['name'] + ' ' + self.data['signup']['surname']

    def Email(self):
        return self.data['signup']['email']

    def UseCase(self):
        # Some special handling is required here. use_case is, as the name
        # suggests is the reason why a new tenant signed up for a GL platform,
        # however, "Other" is allowed as a valid reason, so we need to catch
        # that and send it seperately.
        #
        # The field is currently not subject to internationaliation.
        signup_data = self.data['signup']
        if signup_data['use_case'] == 'other':
            return signup_data['use_case'] + \
                   " - " + \
                   signup_data['use_case_other']
        else:
            return signup_data['use_case']

    def Language(self):
        return self.data['signup']['language']

    def AdminCredentials(self):
        data = {
            'type': 'user_credentials',
            'role': 'admin',
            'username': 'admin',
            'password': self.data['password_admin']
        }

        return Templating().format_template(self.data['notification']['user_credentials'], data) + '\n\n'

    def RecipientCredentials(self):
        data = {
            'type': 'user_credentials',
            'role': 'recipient',
            'username': 'recipient',
            'password': self.data['password_recipient']
        }

        return '\n\n' + Templating().format_template(self.data['notification']['user_credentials'], data)


class AdminPlatformSignupKeyword(PlatformSignupKeyword):
    def RecipientName(self):
        return self.data['user']['name']


class EmailValidationKeyword(UserNodeKeyword):
    keyword_list = NodeKeyword.keyword_list + email_validation_keywords
    data_keys = NodeKeyword.data_keys + \
        ['new_email_address', 'validation_token']

    def NewEmailAddress(self):
        return self.data['new_email_address']

    def UrlPath(self):
        return '/email/validation/' + self.data['validation_token']


class PasswordResetValidation(UserNodeKeyword):
    keyword_list = NodeKeyword.keyword_list + password_reset_validation_keywords

    data_keys = NodeKeyword.data_keys + \
        ['reset_token']

    def UrlPath(self):
        return '/reset/password/' + self.data['reset_token']


class PasswordResetComplete(UserNodeKeyword):
    keyword_list = NodeKeyword.keyword_list + password_reset_complete_keywords

    data_keys = NodeKeyword.data_keys + \
        ['new_password']

    def NewPassword(self):
        return self.data['new_password']


class IdentityAccessRequestKeyword(UserNodeKeyword):
    keyword_list = UserNodeKeyword.keyword_list + identity_access_request_keywords
    data_keys =  UserNodeKeyword.data_keys + ['iar', 'tip', 'user']

    def TipNum(self):
        return str(self.data['tip']['progressive'])

    def UrlPath(self):
        return '/#/custodian/identityaccessrequests/'


class TwoFactorAuthKeyword(NodeKeyword):
    keyword_list = NodeKeyword.keyword_list + two_factor_auth_keywords
    data_keys = ['authcode']

    def AuthCode(self):
        return self.data['authcode']


supported_template_types = {
    u'tip': TipKeyword,
    u'comment': CommentKeyword,
    u'message': MessageKeyword,
    u'file': FileKeyword,
    u'tip_expiration_summary': ExpirationSummaryKeyword,
    u'pgp_alert': PGPAlertKeyword,
    u'admin_pgp_alert': AdminPGPAlertKeyword,
    u'receiver_notification_limit_reached': UserNodeKeyword,
    u'export_template': TipKeyword,
    u'export_message': ExportMessageKeyword,
    u'admin_anomaly': AnomalyKeyword,
    u'admin_test': UserNodeKeyword,
    u'https_certificate_expiration': CertificateExprKeyword,
    u'https_certificate_renewal_failure': CertificateExprKeyword,
    u'software_update_available': SoftwareUpdateKeyword,
    u'admin_signup_alert': AdminPlatformSignupKeyword,
    u'signup': PlatformSignupKeyword,
    u'activation': PlatformSignupKeyword,
    u'email_validation': EmailValidationKeyword,
    u'password_reset_validation': PasswordResetValidation,
    u'password_reset_complete': PasswordResetComplete,
    u'user_credentials': UserCredentials,
    u'identity_access_request': IdentityAccessRequestKeyword,
    u'2fa': TwoFactorAuthKeyword
}


class Templating(object):
    def format_template(self, raw_template, data):
        keyword_converter = supported_template_types[data['type']](data)
        for _ in range(3):
            count = 0

            for kw in keyword_converter.keyword_list:
                if raw_template.count(kw):
                    # if %SomeKeyword% matches, call keyword_converter.SomeKeyword function
                    variable_content = getattr(keyword_converter, kw[1:-1])()
                    raw_template = raw_template.replace(kw, variable_content)

                    count += 1

            # remove lines with only {Blank}
            raw_template = raw_template.replace('\n{Blank}\n', '\n')

            # remove remaining $Blank% tokens
            raw_template = raw_template.replace('\n{Blank}', '')

            raw_template = raw_template.rstrip()

            if count == 0:
                # finally!
                break

        return raw_template

    def get_mail_subject_and_body(self, data):
        subject_template = ''
        body_template = ''

        if data['type'] == 'export_template':
            # this is currently the only template not used for mail notifications
            pass
        elif data['type'] in supported_template_types:
            subject_template = data['notification'][data['type'] + '_mail_title']
            body_template = data['notification'][data['type'] + '_mail_template']
        else:
            raise NotImplementedError('This data_type (%s) is not supported' % ['data.type'])

        if data['type'] in [u'tip', u'comment', u'file', u'message']:
            prefix = '{TipNum} '
            if data['tip']['label']:
                prefix += '[{TipLabel}] '

            subject_template = prefix + subject_template

        subject = self.format_template(subject_template, data)
        body = self.format_template(body_template, data)

        return subject, body
