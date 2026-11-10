import pathlib
import sys
import unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import mail_setup


class MailInputTests(unittest.TestCase):
    def test_empty_or_malformed_sender_reprompts_before_password(self):
        with mock.patch('builtins.input',side_effect=['\t','wrong','sender@gmail.com']) as read, \
             mock.patch('builtins.print'), mock.patch.object(mail_setup.getpass,'getpass') as password:
            self.assertEqual(mail_setup.email_address('Sender'),'sender@gmail.com')
            self.assertEqual(read.call_count,3)
            password.assert_not_called()

    def test_recipient_enter_uses_default(self):
        with mock.patch('builtins.input',return_value=''):
            self.assertEqual(mail_setup.email_address('Recipient','sender@gmail.com'),'sender@gmail.com')

    def test_grouped_password_normalized_and_invalid_input_not_logged(self):
        with mock.patch.object(mail_setup.getpass,'getpass',side_effect=['short','abcd efgh ijkl mnop']), \
             mock.patch('builtins.print') as output:
            self.assertEqual(mail_setup.application_password(),'abcdefghijklmnop')
            self.assertNotIn('short',str(output.call_args_list))
            self.assertNotIn('abcdefghijklmnop',str(output.call_args_list))


if __name__=='__main__':
    unittest.main()
