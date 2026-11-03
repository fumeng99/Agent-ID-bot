"""离线单测（不联网）：编码、指纹、选择器、revert 解码、参数构造、经验库匹配。python3 -m unittest discover -s tests -v"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chain as C, mintlib as L, mintcheck as M


class T(unittest.TestCase):
    def test_sel(self):
        self.assertEqual(C.sel('mint(uint256)'), '0xa0712d68'); self.assertEqual(C.sel('transfer(address,uint256)'), '0xa9059cbb')

    def test_push4(self):
        self.assertEqual(C.push4('0x63a0712d6814'), ['0xa0712d68'])

    def test_fingerprint_ignores_push20_and_meta(self):
        a = '0x73' + '11' * 20 + '00'; b = '0x73' + '22' * 20 + '00'
        self.assertEqual(C.fingerprint(a), C.fingerprint(b))

    def test_revert(self):
        msg = '0x08c379a0' + C.enc_uint(32) + C.enc_uint(5) + b'hello'.hex().ljust(64, '0')
        self.assertEqual(C.decode_revert(msg), 'Error: hello')

    def test_build_args(self):
        f = {'selector': '0x94bf804d', 'inputs': [{'type': 'uint256', 'name': 'qty'}, {'type': 'address', 'name': 'to'}]}
        d = M.build_args(f, '0x' + 'ab' * 20, 2)
        self.assertTrue(d.startswith('0x94bf804d') and d.endswith('ab' * 20))
        self.assertIsNone(M.build_args({'selector': '0x1', 'inputs': [{'type': 'bytes32[]', 'name': 'proof'}]}, '0x' + '0' * 40, 1))

    def test_jaccard(self):
        self.assertEqual(L.jaccard(['a', 'b'], ['a', 'b']), 1.0); self.assertEqual(L.jaccard([], ['a']), 0.0)

    def test_progress_ignores_bool(self):
        self.assertIsNone(M.progress({'genesisFilled()': True, 'GENESIS_WALLET_CAP()': 44}))
        self.assertEqual(M.progress({'totalMinted()': 60, 'maxSupply()': 100})['pct'], 60.0)


if __name__ == '__main__': unittest.main()
