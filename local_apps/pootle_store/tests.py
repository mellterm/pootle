from translate.storage import factory

from pootle.tests import PootleTestCase
from pootle_store.models import Store

class UnitTests(PootleTestCase):
    def setUp(self):
        super(UnitTests, self).setUp()
        self.store = Store.objects.get(pootle_path="/af/pootle/pootle.po")

    def test_getorig(self):
        for dbunit in self.store.units.iterator():
            storeunit = dbunit.getorig()
            self.assertEqual(dbunit.getid(), storeunit.getid())

    def test_convert(self):
        for dbunit in self.store.units.iterator():
            if dbunit.hasplural() and not dbunit.istranslated():
                # skip untranslated plural units, they will always look different
                continue
            storeunit = dbunit.getorig()
            newunit = dbunit.convert(self.store.file.store.UnitClass)
            self.assertEqual(str(newunit), str(storeunit))

    def test_update_target(self):
        self.store.updateunit(0, newvalues={'target': u'samaka'})
        self.store.sync(update_translation=True)
        dbunit = self.store.getitem(0)
        storeunit = dbunit.getorig()

        self.assertEqual(dbunit.target, u'samaka')
        self.assertEqual(dbunit.target, storeunit.target)
        pofile = factory.getobject(self.store.file.path)
        self.assertEqual(dbunit.target, pofile.units[dbunit.index].target)

    def test_update_plural_target(self):
        self.store.updateunit(2, newvalues={'target': [u'samaka', u'samak']})
        self.store.sync(update_translation=True)
        dbunit = self.store.getitem(2)
        storeunit = dbunit.getorig()

        self.assertEqual(dbunit.target.strings, [u'samaka', u'samak'])
        self.assertEqual(dbunit.target.strings, storeunit.target.strings)
        pofile = factory.getobject(self.store.file.path)
        self.assertEqual(dbunit.target.strings, pofile.units[dbunit.index].target.strings)

        self.assertEqual(dbunit.target, u'samaka')
        self.assertEqual(dbunit.target, storeunit.target)
        self.assertEqual(dbunit.target, pofile.units[dbunit.index].target)

    def test_update_plural_target_dict(self):
        self.store.updateunit(2, newvalues={'target': {0: u'samaka', 1: u'samak'}})
        self.store.sync(update_translation=True)
        dbunit = self.store.getitem(2)
        storeunit = dbunit.getorig()

        self.assertEqual(dbunit.target.strings, [u'samaka', u'samak'])
        self.assertEqual(dbunit.target.strings, storeunit.target.strings)
        pofile = factory.getobject(self.store.file.path)
        self.assertEqual(dbunit.target.strings, pofile.units[dbunit.index].target.strings)

        self.assertEqual(dbunit.target, u'samaka')
        self.assertEqual(dbunit.target, storeunit.target)
        self.assertEqual(dbunit.target, pofile.units[dbunit.index].target)

    def test_update_fuzzy(self):
        self.store.updateunit(0, newvalues={'fuzzy': True})
        self.store.sync(update_translation=True)
        dbunit = self.store.getitem(0)
        storeunit = dbunit.getorig()

        self.assertTrue(dbunit.isfuzzy())
        self.assertEqual(dbunit.isfuzzy(), storeunit.isfuzzy())
        pofile = factory.getobject(self.store.file.path)
        self.assertEqual(dbunit.isfuzzy(), pofile.units[dbunit.index].isfuzzy())

        self.store.updateunit(0, newvalues={'fuzzy': False})
        self.store.sync(update_translation=True)
        dbunit = self.store.getitem(0)
        storeunit = dbunit.getorig()

        self.assertFalse(dbunit.isfuzzy())
        self.assertEqual(dbunit.isfuzzy(), storeunit.isfuzzy())
        pofile = factory.getobject(self.store.file.path)
        self.assertEqual(dbunit.isfuzzy(), pofile.units[dbunit.index].isfuzzy())

    def test_update_comment(self):
        self.store.updateunit(0, newvalues={'translator_comments': u'7amada'})
        self.store.sync(update_translation=True)
        dbunit = self.store.getitem(0)
        storeunit = dbunit.getorig()

        self.assertEqual(dbunit.getnotes(origin="translator"), u'7amada')
        self.assertEqual(dbunit.getnotes(origin="translator"), storeunit.getnotes(origin="translator"))
        pofile = factory.getobject(self.store.file.path)
        self.assertEqual(dbunit.getnotes(origin="translator"), pofile.units[dbunit.index].getnotes(origin="translator"))


class StoreTests(PootleTestCase):
    def setUp(self):
        super(StoreTests, self).setUp()
        self.store = Store.objects.get(pootle_path="/af/pootle/pootle.po")

    def test_quickstats(self):
        dbstats = self.store.getquickstats()
        filestats = self.store.file.getquickstats()

        self.assertEqual(dbstats['total'], filestats['total'])
        self.assertEqual(dbstats['totalsourcewords'], filestats['totalsourcewords'])
        self.assertEqual(dbstats['untranslated'], filestats['untranslated'])
        self.assertEqual(dbstats['untranslatedsourcewords'], filestats['untranslatedsourcewords'])
        self.assertEqual(dbstats['fuzzy'], filestats['fuzzy'])
        self.assertEqual(dbstats['fuzzysourcewords'], filestats['fuzzysourcewords'])
        self.assertEqual(dbstats['translated'], filestats['translated'])
        self.assertEqual(dbstats['translatedsourcewords'], filestats['translatedsourcewords'])
        self.assertEqual(dbstats['translatedtargetwords'], filestats['translatedtargetwords'])
