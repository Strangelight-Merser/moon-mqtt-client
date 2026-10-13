"""Independent controls for source-port reuse and causal socket attribution."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from dial_evidence import correlate

PID = 42
LOCAL = ['127.0.0.1', 40000]
REMOTE = ['127.0.0.1', 1883]


def native(life=1, offset=0, success=True, fd=7):
    base = dict(pid=PID, life=life, dial=life, fd=fd, local=LOCAL, remote=REMOTE,
                rc=0, errno=0, so_error=0)
    rows = [dict(base, kind=3, ns=offset),
            dict(base, kind=0, ns=offset+10, rc=-1, errno=115),
            dict(base, kind=1, ns=offset+20, so_error=0 if success else 111),
            dict(base, kind=4, ns=offset+30),
            dict(base, kind=2, ns=offset+80)]
    return rows


def peer(name='candidate', offset=0):
    return [dict(event='accept', peer=name, ns=offset+15, remote=LOCAL, local=REMOTE),
            dict(event='connect', peer=name, ns=offset+25),
            dict(event='connack', peer=name, ns=offset+40, reason=0),
            dict(event='close', peer=name, ns=offset+85)]


class AssociationControls(unittest.TestCase):
    def check(self, sockets, peers):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, rows in [('sockets', sockets), ('peer', peers)]:
                indexed = [dict(row, index=i) for i,row in enumerate(rows)]
                indexed.append(dict(event='buffer', pid=PID, total=len(rows), lost=0))
                (root/f'{name}.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in indexed))
            return correlate(root/'sockets.jsonl', root/'peer.jsonl', PID)

    def test_exact_tuple_and_fd_reuse(self):
        result = self.check(native()+native(2,100), peer()+peer('second',100))
        self.assertEqual([a['peer'] for a in result['attempts']], ['candidate','second'])
        self.assertEqual(len(result['dials']), 2)

    def test_refusal_ignores_historical_accept(self):
        result = self.check(native()+native(2,100,False), peer())
        self.assertEqual(result['attempts'][1]['tcp_error'], 111)
        self.assertIsNone(result['attempts'][1]['peer'])

    def test_other_process_same_tuple_outside_lifetime(self):
        result = self.check(native(), peer()+peer('paho',100))
        self.assertEqual(result['excluded_peer_connections'], 1)
        self.assertEqual(result['attempts'][0]['peer'], 'candidate')

    def test_later_other_process_cannot_replace_missing_candidate(self):
        with self.assertRaisesRegex(ValueError, 'lifetime match'):
            self.check(native(), peer('paho',100))

    def test_ambiguous_accepts(self):
        with self.assertRaisesRegex(ValueError, 'lifetime match'):
            self.check(native(), sorted(peer()+peer('other',1), key=lambda r:r['ns']))

    def test_overlapping_native_tuple_lifetimes(self):
        sockets = sorted(native()+native(2,40,fd=8), key=lambda r:r['ns'])
        with self.assertRaisesRegex(ValueError, 'overlapping native'):
            self.check(sockets, sorted(peer()+peer('second',80), key=lambda r:r['ns']))

    def test_peer_close_before_accept(self):
        rows = peer(); rows[-1]['ns'] = 1
        with self.assertRaisesRegex(ValueError, 'clock order'):
            self.check(native(), rows)

    def test_missing_peer_close(self):
        with self.assertRaisesRegex(ValueError, 'one close'):
            self.check(native(), peer()[:-1])

    def test_close_errno_is_not_tcp_completion(self):
        rows = native(); rows.pop(2); rows[-1]['errno'] = 111
        with self.assertRaisesRegex(ValueError, 'numeric completion'):
            self.check(rows, [])

    def test_failed_getsockopt_errno_is_not_tcp_completion(self):
        rows = native(); rows[2].update(rc=-1, errno=111)
        with self.assertRaisesRegex(ValueError, 'numeric completion'):
            self.check(rows, [])

    def test_refusal_with_in_window_accept_is_invalid(self):
        with self.assertRaisesRegex(ValueError, 'lifetime match'):
            self.check(native(success=False), peer())

    def test_connack_requires_prior_connect(self):
        rows = peer(); rows[1]['event'], rows[2]['event'] = 'connack','connect'
        with self.assertRaisesRegex(ValueError, 'precedes CONNECT'):
            self.check(native(), rows)

    def test_native_clock_reversal(self):
        rows = native(); rows[2]['ns'] = 9
        with self.assertRaisesRegex(ValueError, 'clock order'):
            self.check(rows, peer())


if __name__ == '__main__':
    unittest.main()
