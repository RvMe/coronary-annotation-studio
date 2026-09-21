"""Check shipped QM against source messages and all reader-validation errors."""
import ast
import json
from pathlib import Path
import string
import unittest
import xml.etree.ElementTree as ET
from PySide6.QtCore import QTranslator
from PySide6.QtWidgets import QApplication


ROOT=Path(__file__).resolve().parents[1]/'annotation_app'


class TranslationResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.messages=ET.parse(ROOT/'translations/cas_zh_CN.ts').findall('.//message')
        cls.sources={m.findtext('source') for m in cls.messages}

    def test_every_owned_ui_literal_is_in_the_catalog(self):
        for name in ('app.py','widgets.py','label_catalog.py','i18n.py','platform_support.py'):
            tree=ast.parse((ROOT/name).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='tr' and node.args:
                    source=node.args[0]
                    if isinstance(source,ast.Constant):
                        self.assertIn(source.value,self.sources,(name,node.lineno,source.value))
                    elif isinstance(source,ast.IfExp):
                        for choice in (source.body,source.orelse):
                            self.assertIsInstance(choice,ast.Constant)
                            self.assertIn(choice.value,self.sources)
                    else:
                        # Enumerated sources are checked below. Unknown backend
                        # details deliberately retain the original error text.
                        self.assertIn((name,ast.unparse(source)),{
                            ('app.py','text'),('app.py','tip'),('app.py','template'),
                            ('i18n.py','sources.get(raw, raw)'),
                            ('widgets.py',"{'cpr': 'Longitudinal CPR image', 'cross': 'Orthogonal cross-section image', 'native': 'Native CT image'}[kind]"),
                        })

    def test_enumerated_runtime_sources_are_in_the_catalog(self):
        from annotation_app.label_catalog import REASON_OPTIONS
        sources=['Stenosis: {value}','Confidence: {value}',
                 'Longitudinal CPR image','Orthogonal cross-section image','Native CT image']
        for _,caption,tooltip in REASON_OPTIONS:sources.extend((caption,tooltip))
        for source in sources:self.assertIn(source,self.sources)

    def test_every_reader_validation_error_has_english_source_and_translation(self):
        errors=[]
        for node in ast.walk(ast.parse((ROOT/'label_logic.py').read_text(encoding='utf-8'))):
            if isinstance(node,ast.Raise) and isinstance(node.exc,ast.Call) and node.exc.args:
                text=ast.literal_eval(node.exc.args[0]);errors.append(text)
                self.assertFalse(any('\u4e00'<=c<='\u9fff' for c in text),text)
                self.assertIn(text,self.sources)
        self.assertGreaterEqual(len(errors),17)

    def test_shipped_qm_contains_every_finished_ts_message(self):
        translator=QTranslator();self.assertTrue(translator.load(str(ROOT/'translations/cas_zh_CN.qm')))
        fields=lambda s:sorted((name,spec,conversion) for _,name,spec,conversion in string.Formatter().parse(s) if name is not None)
        self.assertEqual(len(self.messages),len(self.sources))
        for message in self.messages:
            source=message.findtext('source');target=message.findtext('translation')
            self.assertTrue(target,source)
            self.assertNotIn(message.find('translation').get('type'),('unfinished','obsolete','vanished'))
            self.assertEqual(fields(source),fields(target),source)
            self.assertEqual(translator.translate('CAS',source),target,source)

    def test_legacy_error_bridge_uses_the_same_english_sources(self):
        mapping=json.loads((ROOT/'translations/legacy_error_sources.json').read_text(encoding='utf-8'))
        for english in mapping.values():self.assertIn(english,self.sources)


if __name__=='__main__':unittest.main()
