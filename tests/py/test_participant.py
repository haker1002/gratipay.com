from __future__ import print_function, unicode_literals

import datetime
from decimal import Decimal
import random

import mock
import pytest

from aspen.utils import utcnow
from gratipay import NotSane
from gratipay.exceptions import (
    UsernameIsEmpty,
    UsernameTooLong,
    UsernameAlreadyTaken,
    UsernameContainsInvalidCharacters,
    UsernameIsRestricted,
    NoTeam,
    BadAmount,
)
from gratipay.models.account_elsewhere import AccountElsewhere
from gratipay.models.participant import (
    LastElsewhere, NeedConfirmation, NonexistingElsewhere, Participant, TeamCantBeOnlyAuth
)
from gratipay.testing import Harness


# TODO: Test that accounts elsewhere are not considered claimed by default


class TestNeedConfirmation(Harness):
    def test_need_confirmation1(self):
        assert not NeedConfirmation(False, False, False)

    def test_need_confirmation2(self):
        assert NeedConfirmation(False, False, True)

    def test_need_confirmation3(self):
        assert not NeedConfirmation(False, True, False)

    def test_need_confirmation4(self):
        assert NeedConfirmation(False, True, True)

    def test_need_confirmation5(self):
        assert NeedConfirmation(True, False, False)

    def test_need_confirmation6(self):
        assert NeedConfirmation(True, False, True)

    def test_need_confirmation7(self):
        assert NeedConfirmation(True, True, False)

    def test_need_confirmation8(self):
        assert NeedConfirmation(True, True, True)


class TestAbsorptions(Harness):
    # TODO: These tests should probably be moved to absorptions tests
    def setUp(self):
        Harness.setUp(self)
        now = utcnow()
        hour_ago = now - datetime.timedelta(hours=1)
        for i, username in enumerate(['alice', 'bob', 'carl']):
            p = self.make_participant( username
                                     , claimed_time=hour_ago
                                     , last_bill_result=''
                                     , balance=Decimal(i)
                                      )
            setattr(self, username, p)

        deadbeef = self.make_participant('deadbeef', balance=Decimal('18.03'), elsewhere='twitter')
        self.expected_new_balance = self.bob.balance + deadbeef.balance
        deadbeef_twitter = AccountElsewhere.from_user_name('twitter', 'deadbeef')

        self.make_tip(self.carl, self.bob, '1.00')
        self.make_tip(self.alice, deadbeef, '1.00')
        self.bob.take_over(deadbeef_twitter, have_confirmation=True)
        self.deadbeef_archived = Participant.from_id(deadbeef.id)

    def test_participant_can_be_instantiated(self):
        expected = Participant
        actual = Participant.from_username('alice').__class__
        assert actual is expected

    @pytest.mark.xfail(reason="#3399")
    def test_bob_has_two_dollars_in_tips(self):
        expected = Decimal('2.00')
        actual = self.bob.receiving
        assert actual == expected

    def test_alice_gives_to_bob_now(self):
        assert self.get_tip('alice', 'bob') == Decimal('1.00')

    def test_deadbeef_is_archived(self):
        actual = self.db.one( "SELECT count(*) FROM absorptions "
                              "WHERE absorbed_by='bob' AND absorbed_was='deadbeef'"
                             )
        expected = 1
        assert actual == expected

    def test_alice_doesnt_gives_to_deadbeef_anymore(self):
        assert self.get_tip('alice', 'deadbeef') == Decimal('0.00')

    def test_alice_doesnt_give_to_whatever_deadbeef_was_archived_as_either(self):
        assert self.get_tip('alice', self.deadbeef_archived.username) == Decimal('0.00')

    def test_there_is_no_more_deadbeef(self):
        actual = Participant.from_username('deadbeef')
        assert actual is None

    def test_balance_was_transferred(self):
        fresh_bob = Participant.from_username('bob')
        assert fresh_bob.balance == self.bob.balance == self.expected_new_balance
        assert self.deadbeef_archived.balance == 0


class TestTakeOver(Harness):

    def test_cross_tip_doesnt_become_self_tip(self):
        alice_twitter = self.make_elsewhere('twitter', 1, 'alice')
        bob_twitter   = self.make_elsewhere('twitter', 2, 'bob')
        alice = alice_twitter.opt_in('alice')[0].participant
        bob = bob_twitter.opt_in('bob')[0].participant
        self.make_tip(alice, bob, '1.00')
        bob.take_over(alice_twitter, have_confirmation=True)
        self.db.self_check()

    def test_zero_cross_tip_doesnt_become_self_tip(self):
        alice_twitter = self.make_elsewhere('twitter', 1, 'alice')
        bob_twitter   = self.make_elsewhere('twitter', 2, 'bob')
        alice = alice_twitter.opt_in('alice')[0].participant
        bob = bob_twitter.opt_in('bob')[0].participant
        self.make_tip(alice, bob, '1.00')
        self.make_tip(alice, bob, '0.00')

        bob.take_over(alice_twitter, have_confirmation=True)
        self.db.self_check()

    def test_do_not_take_over_zero_tips_giving(self):
        alice_twitter = self.make_elsewhere('twitter', 1, 'alice')
        bob = self.make_elsewhere('twitter', 2, 'bob').opt_in('bob')[0].participant
        carl_twitter  = self.make_elsewhere('twitter', 3, 'carl')
        alice = alice_twitter.opt_in('alice')[0].participant
        carl = carl_twitter.opt_in('carl')[0].participant
        self.make_tip(carl, bob, '1.00')
        self.make_tip(carl, bob, '0.00')
        alice.take_over(carl_twitter, have_confirmation=True)
        ntips = self.db.one("select count(*) from tips")
        assert 2 == ntips
        self.db.self_check()

    def test_do_not_take_over_zero_tips_receiving(self):
        alice_twitter = self.make_elsewhere('twitter', 1, 'alice')
        bob_twitter   = self.make_elsewhere('twitter', 2, 'bob')
        carl_twitter  = self.make_elsewhere('twitter', 3, 'carl')
        alice = alice_twitter.opt_in('alice')[0].participant
        bob = bob_twitter.opt_in('bob')[0].participant
        carl = carl_twitter.opt_in('carl')[0].participant
        self.make_tip(bob, carl, '1.00')
        self.make_tip(bob, carl, '0.00')
        alice.take_over(carl_twitter, have_confirmation=True)
        ntips = self.db.one("select count(*) from tips")
        assert 2 == ntips
        self.db.self_check()

    def test_is_funded_is_correct_for_consolidated_tips_receiving(self):
        alice = self.make_participant('alice', claimed_time='now', balance=1)
        bob = self.make_participant('bob', elsewhere='twitter')
        carl = self.make_participant('carl', elsewhere='github')
        self.make_tip(alice, bob, '1.00')  # funded
        self.make_tip(alice, carl, '5.00')  # not funded
        bob.take_over(('github', str(carl.id)), have_confirmation=True)
        tips = self.db.all("select * from tips where amount > 0 order by id asc")
        assert len(tips) == 3
        assert tips[-1].amount == 6
        assert tips[-1].is_funded is False
        self.db.self_check()

    def test_take_over_fails_if_it_would_result_in_just_a_team_account(self):
        alice_github = self.make_elsewhere('github', 2, 'alice')
        alice = alice_github.opt_in('alice')[0].participant

        a_team_github = self.make_elsewhere('github', 1, 'a_team', is_team=True)
        a_team_github.opt_in('a_team')

        pytest.raises( TeamCantBeOnlyAuth
                     , alice.take_over
                     , a_team_github
                     , have_confirmation=True
                      )

    def test_idempotent(self):
        alice_twitter = self.make_elsewhere('twitter', 1, 'alice')
        bob_github    = self.make_elsewhere('github', 2, 'bob')
        alice = alice_twitter.opt_in('alice')[0].participant
        alice.take_over(bob_github, have_confirmation=True)
        alice.take_over(bob_github, have_confirmation=True)
        self.db.self_check()

    @mock.patch.object(Participant, '_mailer')
    def test_email_addresses_merging(self, mailer):
        alice = self.make_participant('alice')
        alice.add_email('alice@example.com')
        alice.add_email('alice@example.net')
        alice.add_email('alice@example.org')
        alice.verify_email('alice@example.org', alice.get_email('alice@example.org').nonce)
        bob_github = self.make_elsewhere('github', 2, 'bob')
        bob = bob_github.opt_in('bob')[0].participant
        bob.add_email('alice@example.com')
        bob.verify_email('alice@example.com', bob.get_email('alice@example.com').nonce)
        bob.add_email('alice@example.net')
        bob.add_email('bob@example.net')
        alice.take_over(bob_github, have_confirmation=True)

        alice_emails = {e.address: e for e in alice.get_emails()}
        assert len(alice_emails) == 4
        assert alice_emails['alice@example.com'].verified
        assert alice_emails['alice@example.org'].verified
        assert not alice_emails['alice@example.net'].verified
        assert not alice_emails['bob@example.net'].verified

        assert not Participant.from_id(bob.id).get_emails()


    # The below tests were moved up here from TestParticipant, and may be duplicates.

    def hackedSetUp(self):
        now = utcnow()
        for username in ['alice', 'bob', 'carl']:
            p = self.make_participant(username, claimed_time=now, elsewhere='twitter')
            setattr(self, username, p)

    def test_connecting_unknown_account_fails(self):
        self.hackedSetUp()
        with self.assertRaises(NotSane):
            self.bob.take_over(('github', 'jim'))

    def test_cant_take_over_claimed_participant_without_confirmation(self):
        self.hackedSetUp()
        with self.assertRaises(NeedConfirmation):
            self.alice.take_over(('twitter', str(self.bob.id)))

    def test_taking_over_yourself_sets_all_to_zero(self):
        self.hackedSetUp()
        self.make_tip(self.alice, self.bob, '1.00')
        self.alice.take_over(('twitter', str(self.bob.id)), have_confirmation=True)
        expected = Decimal('0.00')
        actual = self.alice.giving
        assert actual == expected

    def test_alice_ends_up_tipping_bob_two_dollars(self):
        self.hackedSetUp()
        self.make_tip(self.alice, self.bob, '1.00')
        self.make_tip(self.alice, self.carl, '1.00')
        self.bob.take_over(('twitter', str(self.carl.id)), have_confirmation=True)
        assert self.get_tip('alice', 'bob') == Decimal('2.00')

    def test_bob_ends_up_tipping_alice_two_dollars(self):
        self.hackedSetUp()
        self.make_tip(self.bob, self.alice, '1.00')
        self.make_tip(self.carl, self.alice, '1.00')
        self.bob.take_over(('twitter', str(self.carl.id)), have_confirmation=True)
        assert self.get_tip('bob', 'alice') == Decimal('2.00')

    def test_ctime_comes_from_the_older_tip(self):
        self.hackedSetUp()
        self.make_tip(self.alice, self.bob, '1.00')
        self.make_tip(self.alice, self.carl, '1.00')
        self.bob.take_over(('twitter', str(self.carl.id)), have_confirmation=True)

        ctimes = self.db.all("""
            SELECT ctime
              FROM tips
             WHERE tipper = 'alice'
               AND tippee = 'bob'
        """)
        assert len(ctimes) == 2
        assert ctimes[0] == ctimes[1]


class TestParticipant(Harness):
    def setUp(self):
        Harness.setUp(self)
        now = utcnow()
        for username in ['alice', 'bob', 'carl']:
            p = self.make_participant(username, claimed_time=now, elsewhere='twitter')
            setattr(self, username, p)

    def test_bob_is_singular(self):
        expected = True
        actual = self.bob.IS_SINGULAR
        assert actual == expected

    def test_john_is_plural(self):
        expected = True
        self.make_participant('john', number='plural')
        actual = Participant.from_username('john').IS_PLURAL
        assert actual == expected

    def test_comparison(self):
        assert self.alice == self.alice
        assert not (self.alice != self.alice)
        assert self.alice != self.bob
        assert not (self.alice == self.bob)
        assert self.alice != None
        assert not (self.alice == None)

    def test_delete_elsewhere_last(self):
        with pytest.raises(LastElsewhere):
            self.alice.delete_elsewhere('twitter', self.alice.id)

    def test_delete_elsewhere_last_signin(self):
        self.make_elsewhere('bountysource', self.alice.id, 'alice')
        with pytest.raises(LastElsewhere):
            self.alice.delete_elsewhere('twitter', self.alice.id)

    def test_delete_elsewhere_nonsignin(self):
        g = self.make_elsewhere('bountysource', 1, 'alice')
        alice = self.alice
        alice.take_over(g)
        accounts = alice.get_accounts_elsewhere()
        assert accounts['twitter'] and accounts['bountysource']
        alice.delete_elsewhere('bountysource', 1)
        accounts = alice.get_accounts_elsewhere()
        assert accounts['twitter'] and accounts.get('bountysource') is None

    def test_delete_elsewhere_nonexisting(self):
        with pytest.raises(NonexistingElsewhere):
            self.alice.delete_elsewhere('github', 1)

    def test_delete_elsewhere(self):
        g = self.make_elsewhere('github', 1, 'alice')
        alice = self.alice
        alice.take_over(g)
        # test preconditions
        accounts = alice.get_accounts_elsewhere()
        assert accounts['twitter'] and accounts['github']
        # do the thing
        alice.delete_elsewhere('twitter', alice.id)
        # unit test
        accounts = alice.get_accounts_elsewhere()
        assert accounts.get('twitter') is None and accounts['github']


class Tests(Harness):

    def random_restricted_username(self):
        """helper method to chooses a restricted username for testing """
        from gratipay import RESTRICTED_USERNAMES
        random_item = random.choice(RESTRICTED_USERNAMES)
        while any(map(random_item.startswith, ('%', '~'))):
            random_item = random.choice(RESTRICTED_USERNAMES)
        return random_item

    def setUp(self):
        Harness.setUp(self)
        self.participant = self.make_participant('user1')  # Our protagonist


    def test_claiming_participant(self):
        now = utcnow()
        self.participant.set_as_claimed()
        actual = self.participant.claimed_time - now
        expected = datetime.timedelta(seconds=0.1)
        assert actual < expected

    def test_changing_username_successfully(self):
        self.participant.change_username('user2')
        actual = Participant.from_username('user2')
        assert self.participant == actual

    def test_changing_username_to_nothing(self):
        with self.assertRaises(UsernameIsEmpty):
            self.participant.change_username('')

    def test_changing_username_to_all_spaces(self):
        with self.assertRaises(UsernameIsEmpty):
            self.participant.change_username('    ')

    def test_changing_username_strips_spaces(self):
        self.participant.change_username('  aaa  ')
        actual = Participant.from_username('aaa')
        assert self.participant == actual

    def test_changing_username_returns_the_new_username(self):
        returned = self.participant.change_username('  foo bar baz  ')
        assert returned == 'foo bar baz', returned

    def test_changing_username_to_too_long(self):
        with self.assertRaises(UsernameTooLong):
            self.participant.change_username('123456789012345678901234567890123')

    def test_changing_username_to_already_taken(self):
        self.make_participant('user2')
        with self.assertRaises(UsernameAlreadyTaken):
            self.participant.change_username('user2')

    def test_changing_username_to_already_taken_is_case_insensitive(self):
        self.make_participant('UsEr2')
        with self.assertRaises(UsernameAlreadyTaken):
            self.participant.change_username('uSeR2')

    def test_changing_username_to_invalid_characters(self):
        with self.assertRaises(UsernameContainsInvalidCharacters):
            self.participant.change_username(u"\u2603") # Snowman

    def test_changing_username_to_restricted_name(self):
        with self.assertRaises(UsernameIsRestricted):
            self.participant.change_username(self.random_restricted_username())


    # id

    def test_participant_gets_a_long_id(self):
        actual = type(self.make_participant('alice').id)
        assert actual == long


    # set_payment_instruction - spi

    def test_spi_sets_payment_instruction(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        alice.set_payment_instruction(team, '1.00')

        actual = alice.get_payment_instruction(team)['amount']
        assert actual == Decimal('1.00')

    def test_spi_returns_a_dict(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        actual = alice.set_payment_instruction(team, '1.00')
        assert isinstance(actual, dict)
        assert isinstance(actual['amount'], Decimal)
        assert actual['amount'] == 1

    def test_spi_allows_up_to_a_thousand(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        alice.set_payment_instruction(team, '1000.00')

    def test_spi_doesnt_allow_a_penny_more(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        self.assertRaises(BadAmount, alice.set_payment_instruction, team, '1000.01')

    def test_spi_allows_a_zero_payment_instruction(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        alice.set_payment_instruction(team, '0.00')

    def test_spi_doesnt_allow_a_penny_less(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        self.assertRaises(BadAmount, alice.set_payment_instruction, team, '-0.01')

    def test_spi_fails_to_set_a_payment_instruction_to_an_unknown_team(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        self.assertRaises(NoTeam, alice.set_payment_instruction, 'The Stargazer', '1.00')

    def test_spi_is_free_rider_defaults_to_none(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        assert alice.is_free_rider is None

    def test_spi_sets_is_free_rider_to_false(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        gratipay = self.make_team('Gratipay', owner=self.make_participant('Gratipay').username)
        alice.set_payment_instruction(gratipay, '0.01')
        assert alice.is_free_rider is False
        assert Participant.from_username('alice').is_free_rider is False

    def test_spi_resets_is_free_rider_to_null(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        gratipay = self.make_team('Gratipay', owner=self.make_participant('Gratipay').username)
        alice.set_payment_instruction(gratipay, '0.00')
        assert alice.is_free_rider is None
        assert Participant.from_username('alice').is_free_rider is None


    # get_teams - gt

    def test_get_teams_gets_teams(self):
        self.make_team(is_approved=True)
        picard = Participant.from_username('picard')
        assert [t.slug for t in picard.get_teams()] == ['TheEnterprise']

    def test_get_teams_can_get_only_approved_teams(self):
        self.make_team(is_approved=True)
        picard = Participant.from_username('picard')
        self.make_team('The Stargazer', owner=picard, is_approved=False)
        assert [t.slug for t in picard.get_teams(only_approved=True)] == ['TheEnterprise']

    def test_get_teams_can_get_all_teams(self):
        self.make_team(is_approved=True)
        picard = Participant.from_username('picard')
        self.make_team('The Stargazer', owner=picard, is_approved=False)
        assert [t.slug for t in picard.get_teams()] == ['TheEnterprise', 'TheStargazer']


    # giving

    def test_giving_only_includes_funded_payment_instructions(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        bob = self.make_participant('bob', claimed_time='now')
        carl = self.make_participant('carl', claimed_time='now', last_bill_result="Fail!")
        team = self.make_team(is_approved=True)

        alice.set_payment_instruction(team, '3.00') # The only funded tip
        bob.set_payment_instruction(team, '5.00')
        carl.set_payment_instruction(team, '7.00')

        assert alice.giving == Decimal('3.00')
        assert bob.giving == Decimal('0.00')
        assert carl.giving == Decimal('0.00')

        funded_tip = self.db.one("SELECT * FROM payment_instructions WHERE is_funded ORDER BY id")
        assert funded_tip.participant == alice.username

    def test_giving_only_includes_the_latest_payment_instruction(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team(is_approved=True)

        alice.set_payment_instruction(team, '12.00')
        alice.set_payment_instruction(team, '4.00')

        assert alice.giving == Decimal('4.00')


    # get_age_in_seconds - gais

    def test_gais_gets_age_in_seconds(self):
        now = utcnow()
        alice = self.make_participant('alice', claimed_time=now)
        actual = alice.get_age_in_seconds()
        assert 0 < actual < 1

    def test_gais_returns_negative_one_if_None(self):
        alice = self.make_participant('alice', claimed_time=None)
        actual = alice.get_age_in_seconds()
        assert actual == -1


    # resolve_unclaimed - ru

    def test_ru_returns_None_for_orphaned_participant(self):
        resolved = self.make_participant('alice').resolve_unclaimed()
        assert resolved is None, resolved

    def test_ru_returns_bitbucket_url_for_stub_from_bitbucket(self):
        unclaimed = self.make_elsewhere('bitbucket', '1234', 'alice')
        stub = Participant.from_username(unclaimed.participant.username)
        actual = stub.resolve_unclaimed()
        assert actual == "/on/bitbucket/alice/"

    def test_ru_returns_github_url_for_stub_from_github(self):
        unclaimed = self.make_elsewhere('github', '1234', 'alice')
        stub = Participant.from_username(unclaimed.participant.username)
        actual = stub.resolve_unclaimed()
        assert actual == "/on/github/alice/"

    def test_ru_returns_twitter_url_for_stub_from_twitter(self):
        unclaimed = self.make_elsewhere('twitter', '1234', 'alice')
        stub = Participant.from_username(unclaimed.participant.username)
        actual = stub.resolve_unclaimed()
        assert actual == "/on/twitter/alice/"

    def test_ru_returns_openstreetmap_url_for_stub_from_openstreetmap(self):
        unclaimed = self.make_elsewhere('openstreetmap', '1', 'alice')
        stub = Participant.from_username(unclaimed.participant.username)
        actual = stub.resolve_unclaimed()
        assert actual == "/on/openstreetmap/alice/"


    # archive

    def test_archive_fails_for_team_owner(self):
        alice = self.make_participant('alice')
        self.make_team(owner=alice)
        with self.db.get_cursor() as cursor:
            pytest.raises(alice.StillATeamOwner, alice.archive, cursor)

    def test_archive_fails_if_balance_is_positive(self):
        alice = self.make_participant('alice', balance=2)
        with self.db.get_cursor() as cursor:
            pytest.raises(alice.BalanceIsNotZero, alice.archive, cursor)

    def test_archive_fails_if_balance_is_negative(self):
        alice = self.make_participant('alice', balance=-2)
        with self.db.get_cursor() as cursor:
            pytest.raises(alice.BalanceIsNotZero, alice.archive, cursor)

    def test_archive_clears_claimed_time(self):
        alice = self.make_participant('alice')
        with self.db.get_cursor() as cursor:
            archived_as = alice.archive(cursor)
        assert Participant.from_username(archived_as).claimed_time is None

    def test_archive_records_an_event(self):
        alice = self.make_participant('alice')
        with self.db.get_cursor() as cursor:
            archived_as = alice.archive(cursor)
        payload = self.db.one("SELECT * FROM events WHERE payload->>'action' = 'archive'").payload
        assert payload['values']['old_username'] == 'alice'
        assert payload['values']['new_username'] == archived_as


    # suggested_payment

    def test_suggested_payment_is_zero_for_new_user(self):
        alice = self.make_participant('alice')
        assert alice.suggested_payment == 0
