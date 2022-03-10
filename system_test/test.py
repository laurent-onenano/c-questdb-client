#!/usr/bin/env python3

import sys
sys.dont_write_bytecode = True

import time
import datetime
import argparse
import unittest
import questdb_linesender as qls
import uuid
from fixture import (
    QuestDbFixture,
    install_questdb,
    list_questdb_releases,
    retry)
import urllib.request
import urllib.parse
import json

QDB_FIXTURE: QuestDbFixture = None


class QueryError(Exception):
    pass


def http_sql_query(sql_query):
    url = (
        f'http://localhost:{QDB_FIXTURE.http_server_port}/exec?' +
        urllib.parse.urlencode({'query': sql_query}))
    resp = urllib.request.urlopen(url, timeout=0.2)
    if resp.status != 200:
        raise RuntimeError(f'Error response {resp.status} from {sql_query!r}')
    buf = resp.read()
    try:
        data = json.loads(buf)
    except json.JSONDecodeError as jde:
        # Include buffer in error message for easier debugging.
        raise json.JSONDecodeError(
            f'Could not parse response: {buf!r}: {jde.msg}',
            jde.doc,
            jde.pos)
    if 'error' in data:
        raise QueryError(data['error'])
    return data


def retry_check_table(table_name, min_rows=1, timeout_sec=5):
    def check_table():
        try:
            resp = http_sql_query(f"select * from '{table_name}'")
            if not resp.get('dataset'):
                return False
            elif len(resp['dataset']) < min_rows:
                return False
            return resp
        except QueryError:
            return None

    return retry(check_table, timeout_sec=timeout_sec)


class TestSomething(unittest.TestCase):
    def test_insert_three_rows(self):
        table_name = uuid.uuid4().hex
        with qls.Sender('localhost', QDB_FIXTURE.line_tcp_port) as sender:
            for _ in range(3):
                (sender
                    .table(table_name)
                    .symbol('name_a', 'val_a')
                    .column('name_b', True)
                    .column('name_c', 42)
                    .column('name_d', 2.5)
                    .column('name_e', 'val_b')
                    .at_now())

        resp = retry_check_table(table_name)
        exp_columns = [
            {'name': 'name_a', 'type': 'SYMBOL'},
            {'name': 'name_b', 'type': 'BOOLEAN'},
            {'name': 'name_c', 'type': 'LONG'},
            {'name': 'name_d', 'type': 'DOUBLE'},
            {'name': 'name_e', 'type': 'STRING'},
            {'name': 'timestamp', 'type': 'TIMESTAMP'}]
        self.assertEqual(resp['columns'], exp_columns)

        exp_dataset = [  # Comparison excludes timestamp column.
            ['val_a', True, 42, 2.5, 'val_b'],
            ['val_a', True, 42, 2.5, 'val_b'],
            ['val_a', True, 42, 2.5, 'val_b']]
        scrubbed_dataset = [row[:-1] for row in resp['dataset']]
        self.assertEqual(scrubbed_dataset, exp_dataset)

    def test_repeated_symbol_and_column_names(self):
        if QDB_FIXTURE.version <= (6, 1, 2):
            self.skipTest('No support for duplicate column names.')
            return
        table_name = uuid.uuid4().hex
        with qls.Sender('localhost', QDB_FIXTURE.line_tcp_port) as sender:
            (sender
                .table(table_name)
                .symbol('a', 'A')
                .symbol('a', 'B')
                .column('b', False)
                .column('b', 'C')
                .at_now())

        resp = retry_check_table(table_name)
        exp_columns = [
            {'name': 'a', 'type': 'SYMBOL'},
            {'name': 'b', 'type': 'BOOLEAN'},
            {'name': 'timestamp', 'type': 'TIMESTAMP'}]
        self.assertEqual(resp['columns'], exp_columns)

        exp_dataset = [['A', False]]  # Comparison excludes timestamp column.
        scrubbed_dataset = [row[:-1] for row in resp['dataset']]
        self.assertEqual(scrubbed_dataset, exp_dataset)

    def test_same_symbol_and_col_name(self):
        if QDB_FIXTURE.version <= (6, 1, 2):
            self.skipTest('No support for duplicate column names.')
            return
        table_name = uuid.uuid4().hex
        with qls.Sender('localhost', QDB_FIXTURE.line_tcp_port) as sender:
            (sender
                .table(table_name)
                .symbol('a', 'A')
                .column('a', 'B')
                .at_now())

        resp = retry_check_table(table_name)
        exp_columns = [
            {'name': 'a', 'type': 'SYMBOL'},
            {'name': 'timestamp', 'type': 'TIMESTAMP'}]
        self.assertEqual(resp['columns'], exp_columns)

        exp_dataset = [['A']]  # Comparison excludes timestamp column.
        scrubbed_dataset = [row[:-1] for row in resp['dataset']]
        self.assertEqual(scrubbed_dataset, exp_dataset)

    def test_single_symbol(self):
        table_name = uuid.uuid4().hex
        with qls.Sender('localhost', QDB_FIXTURE.line_tcp_port) as sender:
            (sender
                .table(table_name)
                .symbol('a', 'A')
                .at_now())

        resp = retry_check_table(table_name)
        exp_columns = [
            {'name': 'a', 'type': 'SYMBOL'},
            {'name': 'timestamp', 'type': 'TIMESTAMP'}]
        self.assertEqual(resp['columns'], exp_columns)

        exp_dataset = [['A']]  # Comparison excludes timestamp column.
        scrubbed_dataset = [row[:-1] for row in resp['dataset']]
        self.assertEqual(scrubbed_dataset, exp_dataset)

    def test_two_columns(self):
        table_name = uuid.uuid4().hex
        with qls.Sender('localhost', QDB_FIXTURE.line_tcp_port) as sender:
            (sender
                .table(table_name)
                .column('a', 'A')
                .column('b', 'B')
                .at_now())

        resp = retry_check_table(table_name)
        exp_columns = [
            {'name': 'a', 'type': 'STRING'},
            {'name': 'b', 'type': 'STRING'},
            {'name': 'timestamp', 'type': 'TIMESTAMP'}]
        self.assertEqual(resp['columns'], exp_columns)

        exp_dataset = [['A', 'B']]  # Comparison excludes timestamp column.
        scrubbed_dataset = [row[:-1] for row in resp['dataset']]
        self.assertEqual(scrubbed_dataset, exp_dataset)

    def test_mismatched_types_across_rows(self):
        table_name = uuid.uuid4().hex
        with qls.Sender('localhost', QDB_FIXTURE.line_tcp_port) as sender:
            (sender
                .table(table_name)
                .symbol('a', 'A')  # SYMBOL
                .at_now())
            (sender
                .table(table_name)
                .column('a', 'B')  # STRING
                .at_now())

        # We only ever get the first row back.
        resp = retry_check_table(table_name)
        exp_columns = [
            {'name': 'a', 'type': 'SYMBOL'},
            {'name': 'timestamp', 'type': 'TIMESTAMP'}]
        self.assertEqual(resp['columns'], exp_columns)

        exp_dataset = [['A']]  # Comparison excludes timestamp column.
        scrubbed_dataset = [row[:-1] for row in resp['dataset']]
        self.assertEqual(scrubbed_dataset, exp_dataset)

        # The second one is dropped and will not appear in results.
        with self.assertRaises(TimeoutError):
            retry_check_table(table_name, min_rows=2, timeout_sec=1)

    def test_at(self):
        if QDB_FIXTURE.version <= (6, 0, 7, 1):
            self.skipTest('No support for user-provided timestamps.')
            return
        table_name = uuid.uuid4().hex
        at_ts_ns = 1647357688714369403
        with qls.Sender('localhost', QDB_FIXTURE.line_tcp_port) as sender:
            (sender
                .table(table_name)
                .symbol('a', 'A')
                .at(at_ts_ns))

        # We first need to match QuestDB's internal microsecond resolution.
        at_ts_us = int(at_ts_ns / 1000.0)
        at_ts_sec = at_ts_us / 1000000.0
        at_td = datetime.datetime.fromtimestamp(at_ts_sec)
        at_str = at_td.isoformat() + 'Z'

        resp = retry_check_table(table_name)
        exp_dataset = [['A', at_str]]
        self.assertEqual(resp['dataset'], exp_dataset)

    def test_underscores(self):
        table_name = f'_{uuid.uuid4().hex}_'
        with qls.Sender('localhost', QDB_FIXTURE.line_tcp_port) as sender:
            (sender
                .table(table_name)
                .symbol('_a_b_c_', 'A')
                .column('_d_e_f_', True)
                .at_now())

        resp = retry_check_table(table_name)
        exp_columns = [
            {'name': '_a_b_c_', 'type': 'SYMBOL'},
            {'name': '_d_e_f_', 'type': 'BOOLEAN'},
            {'name': 'timestamp', 'type': 'TIMESTAMP'}]
        self.assertEqual(resp['columns'], exp_columns)

        exp_dataset = [['A', True]]  # Comparison excludes timestamp column.
        scrubbed_dataset = [row[:-1] for row in resp['dataset']]
        self.assertEqual(scrubbed_dataset, exp_dataset)

    def test_funky_chars(self):
        if QDB_FIXTURE.version <= (6, 0, 7, 1):
            self.skipTest('No unicode support.')
            return
        table_name = uuid.uuid4().hex
        smilie = b'\xf0\x9f\x98\x81'.decode('utf-8')
        with qls.Sender('localhost', QDB_FIXTURE.line_tcp_port) as sender:
            sender.table(table_name)
            sender.symbol(smilie, smilie)
            # for num in range(1, 32):
            #     char = chr(num)
            #     sender.column(char, char)
            sender.at_now()

        resp = retry_check_table(table_name)
        exp_columns = [
            {'name': smilie, 'type': 'SYMBOL'},
            {'name': 'timestamp', 'type': 'TIMESTAMP'}]
        self.assertEqual(resp['columns'], exp_columns)

        exp_dataset = [[smilie]]  # Comparison excludes timestamp column.
        scrubbed_dataset = [row[:-1] for row in resp['dataset']]
        self.assertEqual(scrubbed_dataset, exp_dataset)


def parse_args():
    parser = argparse.ArgumentParser('Run system tests.')
    sub_p = parser.add_subparsers(dest='command')
    run_p = sub_p.add_parser('run', help='Run tests')
    run_p.add_argument(
        '--unittest-help',
        action='store_true',
        help='Show unittest --help')
    version_g = run_p.add_mutually_exclusive_group()
    version_g.add_argument(
        '--last-n',
        type=int,
        help='test against last N versions')
    version_g.add_argument(
        '--versions',
        type=str,
        nargs='+',
        help='List of versions, e.g. `6.1.2`.')
    list_p = sub_p.add_parser('list', help='List latest -n releases.')
    list_p.set_defaults(command='list')
    list_p.add_argument('-n', type=int, default=30, help='number of releases')
    return parser.parse_known_args()


def list(args):
    print('List of releases:')
    for vers, _ in list_questdb_releases(args.n or 1):
        print(f'    {vers}')


def run(args, show_help=False):
    if show_help:
        sys.argv.append('--help')
        unittest.main()
        return

    last_n = 1
    if getattr(args, 'last_n', None):
        last_n = args.last_n
    elif getattr(args, 'versions', None):
        last_n = 30  # Hack, can't test older releases.
    versions = {
        vers: download_url
        for vers, download_url
        in list_questdb_releases(last_n)}
    versions_args = getattr(args, 'versions', None)
    if versions_args:
        versions = {
            vers: versions[vers]
            for vers in versions_args}

    global QDB_FIXTURE
    for version, download_url in versions.items():
        questdb_dir = install_questdb(version, download_url)
        QDB_FIXTURE = QuestDbFixture(questdb_dir)
        try:
            QDB_FIXTURE.start()
            test_prog = unittest.TestProgram(exit=False)
            if not test_prog.result.wasSuccessful():
                sys.exit(1)
        finally:
            QDB_FIXTURE.stop()


def main():
    args, extra_args = parse_args()
    if args.command == 'list':
        list(args)
    else:
        # Repackage args for unittests own arg parser.
        sys.argv[:] = sys.argv[:1] + extra_args
        show_help = getattr(args, 'unittest_help', False)
        run(args, show_help)


if __name__ == '__main__':
    main()
