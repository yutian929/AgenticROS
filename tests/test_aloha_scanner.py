"""Host-only asset integrity and switch parity; no robot process or native deps."""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ALOHA = ROOT/'docker/robot_instances/aloha_tabletop'
ASSET = ALOHA/'scanner_asset'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


scanner = module('aloha_scanner', ALOHA/'scanner.py')
original = module('original_scanner', ROOT/'docker/robot_instances/quanta_x1/scanner_device.py')
prepare = module('aloha_scene', ALOHA/'prepare_scene.py')


class ScannerAsset(unittest.TestCase):
    def test_texture_and_exported_geometry_match_pinned_source(self):
        manifest = json.loads((ASSET/'manifest.json').read_text())
        source = ROOT/'docker/robot_instances/quanta_x1/scanner_asset'
        self.assertEqual((ASSET/'albedo.png').read_bytes(), (source/'albedo.png').read_bytes())
        self.assertEqual(hashlib.sha256((source/'scanner.usdc').read_bytes()).hexdigest(),
                         manifest['split_usd_sha256'])
        self.assertEqual(sum(manifest['meshes'][p]['faces'] for p in ('body', 'trigger')), 50000)
        self.assertEqual(sum(n.startswith('hull_') for n in manifest['meshes']), 22)
        for name, metadata in manifest['meshes'].items():
            data = (ASSET/(name+'.obj')).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), metadata['sha256'])
            lines = data.decode().splitlines()
            self.assertEqual(sum(s.startswith('f ') for s in lines), metadata['faces'])
            if name in ('body', 'trigger'):
                self.assertEqual(sum(s.startswith('vt ') for s in lines), metadata['vertices'])
                self.assertEqual(sum(s.startswith('vn ') for s in lines), metadata['vertices'])

    def test_scene_preparation_is_repeatable_and_preserves_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            shutil.copytree(ALOHA/'model', target/'model')
            shutil.copytree(ASSET, target/'scanner_asset')
            shutil.copyfile(ALOHA/'layout.json', target/'layout.json')
            before = {p: p.read_bytes() for p in (target/'model').glob('*.xml')}
            prepare.prepare(target/'model')
            scene = (target/'model/agenticros_scene.xml').read_bytes()
            prepare.prepare(target/'model')
            self.assertEqual(scene, (target/'model/agenticros_scene.xml').read_bytes())
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)


class SwitchParity(unittest.TestCase):
    def test_press_hold_release_and_repress_match_isaac_device(self):
        switches = [scanner.MechanicalSwitch(), original.MechanicalSwitch()]
        trace = [(0., 0.), (.0045, .01), (.0045, .02), (0., .03),
                 (.005, .1), (.005, .14), (.005, .5), (.002, .6),
                 (.001, .7), (.005, .8), (.005, .84)]
        for travel, now in trace:
            for switch in switches:
                switch.observe_switch(travel, now)
            self.assertEqual(switches[0].status(), switches[1].status())
        self.assertEqual(switches[0].press_count, 2)
        self.assertTrue(switches[0].pressed)

    def test_nonfinite_feedback_is_rejected(self):
        for travel, now in [(math.nan, 0.), (0., math.inf)]:
            with self.assertRaises(ValueError):
                scanner.MechanicalSwitch().observe_switch(travel, now)


if __name__ == '__main__':
    unittest.main()
