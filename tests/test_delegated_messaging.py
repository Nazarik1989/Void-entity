import unittest

import delegated_messaging as dm


class DelegatedMessagingTests(unittest.TestCase):
    def test_natural_request_and_russian_dative_alias(self):
        alias, purpose = dm.parse_delegation_request("Напиши Диману, чтобы позвонил насчёт тачки")
        self.assertEqual(alias, "Диману")
        self.assertIn("тачки", purpose)
        self.assertEqual(dm.resolve_saved_contact([{"alias": "Диман", "chat_id": 42}], alias)["chat_id"], 42)

    def test_void_introduction_is_disclosed(self):
        delegation = dm.create_delegation(
            character_id="void", owner_user_id=1, contact_chat_id=2,
            contact_name="Диман", purpose="обсудить звонок насчёт машины",
        )
        text = dm.introduction(delegation)
        self.assertIn("VOID", text)
        self.assertIn("AI-помощник", text)
        self.assertIn("Назар попросил", text)

    def test_safety_guards(self):
        self.assertIn("secret", dm.assess_risk("пришли пароль"))
        self.assertTrue(dm.is_stop("не пиши"))

    def test_ambiguous_alias_is_not_guessed(self):
        contacts = [{"alias": "Диман", "chat_id": 1}, {"alias": "Диман", "chat_id": 2}]
        self.assertIsNone(dm.resolve_saved_contact(contacts, "Диману"))


if __name__ == "__main__":
    unittest.main()
