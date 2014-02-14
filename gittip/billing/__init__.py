"""This module encapsulates billing logic and db access.

There are three pieces of information for each participant related to billing:

    balanced_account_uri
        * NULL - This participant has never been billed.
        * 'deadbeef' - This participant has had a Balanced account created for
          them, either by adding a credit card or a bank account.
    last_bill_result
        * NULL - This participant has not had their credit card charged yet.
        * '' - This participant has a working card.
        * <message> - An error message.
    last_ach_result
        * NULL - This participant has not wired up a bank account yet.
        * '' - This participant has a working bank account.
        * <message> - An error message.

"""
from __future__ import unicode_literals
from urllib import quote

import gittip
import balanced
import stripe
from aspen.utils import typecheck


def get_balanced_account(db, username, balanced_account_uri):
    """Find or create a balanced.Account.
    """
    typecheck( username, unicode
             , balanced_account_uri, (unicode, None)
              )

    # XXX Balanced requires an email address
    # https://github.com/balanced/balanced-api/issues/20
    # quote to work around https://github.com/gittip/www.gittip.com/issues/781
    # emails are not required for customers any more
    email_address = '{}@gittip.com'.format(quote(username))


    if balanced_account_uri is None:
        try:
            customer = \
               balanced.customers.query.filter(email=email_address).one()
        except balanced.exc.NoResultFound:
            customer = balanced.Customer(email=email_address).save()
        BALANCED_ACCOUNT = """\

                UPDATE participants
                   SET balanced_account_uri=%s
                 WHERE username=%s

        """
        db.run(BALANCED_ACCOUNT, (account.uri, username))
        customer.meta['username'] = username
        customer.save()  # HTTP call under here
    else:
        customer = balanced.Customer.fetch(balanced_account_uri)
    return customer


def associate(db, thing, username, balanced_account_uri, balanced_thing_uri):
    """Given four unicodes, return a unicode.

    This function attempts to associate the credit card or bank account details
    referenced by balanced_thing_uri with a Balanced Account. If it fails we
    log and return a unicode describing the failure. Even for failure we keep
    balanced_account_uri; we don't reset it to None/NULL. It's useful for
    loading the previous (bad) info from Balanced in order to prepopulate the
    form.

    """
    typecheck( username, unicode
             , balanced_account_uri, (unicode, None, balanced.Account)
             , balanced_thing_uri, unicode
             , thing, unicode
              )

    if isinstance(balanced_account_uri, balanced.Account):
        balanced_account = balanced_account_uri
    else:
        balanced_account = get_balanced_account( db
                                               , username
                                               , balanced_account_uri
                                                )
    invalidate_on_balanced(thing, balanced_account.uri)
    SQL = "UPDATE participants SET last_%s_result=%%s WHERE username=%%s"

    if thing == "credit card":
        add = balanced_account.add_card
        SQL %= "bill"
    else:
        assert thing == "bank account", thing # sanity check
        add = balanced_account.add_bank_account
        SQL %= "ach"

    try:
        add(balanced_thing_uri)
    except balanced.exc.HTTPError as err:
        error = err.message.decode('UTF-8')  # XXX UTF-8?
    else:
        error = ''
    typecheck(error, unicode)

    db.run(SQL, (error, username))
    return error


def invalidate_on_balanced(thing, balanced_account_uri):
    """XXX Things in balanced cannot be deleted at the moment.

    Instead we mark all valid cards as invalid which will restrict against
    anyone being able to issue charges against them in the future.

    See: https://github.com/balanced/balanced-api/issues/22

    """
    assert thing in ("credit card", "bank account")
    typecheck(balanced_account_uri, unicode)

    customer = balanced.Customer.fetch(balanced_account_uri)
    things = customer.cards if thing == "credit card" else customer.bank_accounts

    for _thing in things:
        _thing.unstore()


def clear(db, thing, username, balanced_account_uri):
    typecheck( thing, unicode
             , username, unicode
             , balanced_account_uri, unicode
              )
    assert thing in ("credit card", "bank account"), thing
    invalidate_on_balanced(thing, balanced_account_uri)
    CLEAR = """\

        UPDATE participants
           SET last_%s_result=NULL
         WHERE username=%%s

    """ % ("bill" if thing == "credit card" else "ach")
    db.run(CLEAR, (username,))


def store_error(db, thing, username, msg):
    typecheck(thing, unicode, username, unicode, msg, unicode)
    assert thing in ("credit card", "bank account"), thing
    ERROR = """\

        UPDATE participants
           SET last_%s_result=%%s
         WHERE username=%%s

    """ % ("bill" if thing == "credit card" else "ach")
    db.run(ERROR, (msg, username))


# Card
# ====
# While we're migrating data we need to support loading data from both Stripe
# and Balanced.

class StripeCard(object):
    """This is a dict-like wrapper around a Stripe PaymentMethod.
    """

    _customer = None  # underlying stripe.Customer object

    def __init__(self, stripe_customer_id):
        """Given a Stripe customer id, load data from Stripe.
        """
        if stripe_customer_id is not None:
            self._customer = stripe.Customer.retrieve(stripe_customer_id)

    def _get(self, name, default=""):
        """Given a name, return a string.
        """
        out = ""
        if self._customer is not None:
            out = self._customer.get('active_card', {}).get(name, "")
            if out is None:
                out = default
        return out

    def __getitem__(self, name):
        """Given a name, return a string.
        """
        if name == 'id':
            out = self._customer.id if self._customer is not None else None
        elif name == 'last4':
            out = self._get('last4')
            if out:
                out = "************" + out
        else:
            name = { 'address_1': 'address_line1'
                   , 'address_2': 'address_line2'
                   , 'state': 'address_state'
                   , 'zip': 'address_zip'
                    }.get(name, name)
            out = self._get(name)
        return out


class BalancedThing(object):
    """Represent either a credit card or a bank account.
    """

    thing_type = None

    _customer = None    # underlying balanced.Customer object
    _thing = None       # underlying balanced.{BankAccount,Card} object

    def _get(self, name, default=""):
        """Given a name, return a unicode.
        """
        out = None
        if self._customer is not None and self._thing is not None:
            #try:
                #out = getattr(self._thing, name, None)
            out = self._thing
            for val in name.split('.'):
                if type(out) is dict:
                    out = out.get(val)
                else:
                    out = getattr(out, val)
                if out is None:
                    break

            #except IndexError:  # ?? still needed
            #    pass
        if out is None:
            out = default
        return out

    def __init__(self, balanced_account_uri):
        """Given a Balanced account_uri, load data from Balanced.
        """
        if balanced_account_uri is None:
            return

        # XXX Indexing is borken. See:
        # https://github.com/balanced/balanced-python/issues/10

        self._customer = balanced.Customer.fetch(balanced_account_uri)

        things = getattr(self._customer, self.thing_type+'s').filter(is_valid=True).all()

        #things = getattr(self._customer, self.thing_type+'s').all()
        #things = [thing for thing in things if thing.is_valid]
        nvalid = len(things)

        if nvalid == 0:
            self._thing = None
        elif nvalid == 1:
            self._thing = things[0]
        else:
            msg = "%s has %d valid %ss"
            msg %= (balanced_account_uri, len(things), self.thing_type)
            raise RuntimeError(msg)

    @property
    def is_setup(self):
        return self._thing is not None


class BalancedCard(BalancedThing):
    """This is a dict-like wrapper around a Balanced Account.
    """

    thing_type = 'card'

    def __getitem__(self, name):
        """Given a name, return a string.
        """
        #import ipdb; ipdb.set_trace()

        if name == 'id':
            out = self._customer.href if self._customer is not None else None
        else:
            name = {
                'address_1': 'address.line1',
                'address_2': 'meta.address_2',
                'country': 'meta.country',
                'city_town': 'meta.city_town',
                'zip': 'address.postal_code',
                'state': 'meta.region',  # 'address.state', # noted error bellow, are the saving it in both places?
                'last4': 'number',
                'last_four': 'number',
            }.get(name, name)
            out = self._get(name)

        # elif name == 'last4':
        #     out = self._get('number')

        # elif name == 'address_2':
        #     out = self._get('meta', {}).get('address_2', '')

        # elif name == 'country':
        #     out = self._get('meta', {}).get('country', '')

        # elif name == 'city_town':
        #     out = self._get('meta', {}).get('city_town', '')

        # elif name == 'state':
        #     out = self._get('region')
        #     if not out:
        #         # There's a bug in balanced where the region does get persisted
        #         # but doesn't make it back out. This is a workaround until such
        #         # time as that's fixed.
        #         out = self._get('meta', {}).get('region', '')
        # else:
        #     name = { 'address_1': 'street_address'
        #            , 'zip': 'postal_code'
        #             }.get(name, name)
        #     out = self._get(name)
        return out


class BalancedBankAccount(BalancedThing):
    """This is a dict-like wrapper around a Balanced Account.
    """

    thing_type = 'bank_account'

    def __getitem__(self, item):
        #import ipdb; ipdb.set_trace()
        mapper = {
            'id': 'href',
            'customer_href': 'customer.href',
            'account_uri': 'customer.href',  # TODO: remove
            'bank_name': 'bank_name',
            'last_four': 'last_four',
        }
        if item not in mapper:
            raise IndexError()
        if not self._thing:
            return None


        # Do goofiness to support 'account.uri' in mapper.
        # ================================================
        # An account.uri access unrolls to:
        #     _item = getattr(self._thing, 'account')
        #     _item = getattr(_item, 'uri')

        return self._get(mapper[item])

        # _item = self._thing
        # for val in mapper[item].split('.'):
        #     _item = getattr(_item, val)
        # return _item
