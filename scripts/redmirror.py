#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ManaLAB - redmirror
Τραβάει ΜΟΝΟ τα addons που δηλώνονται στο WANTED από το repo του RedWizard
και τα καθρεφτίζει στον φάκελο redmirror/ ώστε οι συσκευές να μην εξαρτώνται
από τον server τους.
"""

import codecs
import gzip
import hashlib
import io
import os
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

# --- ρυθμίσεις -------------------------------------------------------------

# Πρόσθεσε εδώ όποιο addon id θες να καθρεφτίζεται
WANTED = [
    'plugin.video.redlight',
    'plugin.audio.mp3streams',
]

# Ψάχνει και στα δύο, κρατάει τη νεότερη έκδοση που θα βρει
SOURCES = [
    'https://repo.redwizard.xyz/redwizardrepo/main/',
    'https://repo.redwizard.xyz/redwizardrepo/21omega/',
    'https://repo.redwizard.xyz/redwizardrepo/22piers/',
]

OUT_DIR = 'redmirror'
KEEP = 2          # πόσες εκδόσεις κρατάμε ανά addon
TIMEOUT = 60
UA = {'User-Agent': 'Mozilla/5.0'}


# --- βοηθητικά -------------------------------------------------------------

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read()
    # κάποιοι servers στέλνουν gzip ακόμα κι όταν λέει compressed="false"
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return data


def parse_xml(data, where):
    """Παρσάρει XML αγνοώντας BOM/σκουπίδια μπροστά. Σε αποτυχία δείχνει τι ήρθε."""
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8):]
    data = data.lstrip()
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        print('! δεν παρσάρεται το %s -> %s' % (where, e))
        print('  τι ήρθε (πρώτα 200 bytes): %r' % data[:200])
        return None


def vkey(version):
    """Σύγκριση εκδόσεων τύπου 1.2.10 > 1.2.9 (όχι αλφαβητικά)."""
    return tuple(int(n) for n in re.findall(r'\d+', version)) or (0,)


def remote_versions():
    """Επιστρέφει {addon_id: (version, base_url)} με τη νεότερη έκδοση."""
    found = {}
    for base in SOURCES:
        try:
            data = fetch(base + 'addons.xml')
        except Exception as e:
            print('! δεν διαβάστηκε %saddons.xml -> %s' % (base, e))
            continue
        root = parse_xml(data, base + 'addons.xml')
        if root is None:
            continue
        for addon in root.findall('addon'):
            aid = addon.get('id')
            ver = addon.get('version')
            if aid not in WANTED or not ver:
                continue
            if aid not in found or vkey(ver) > vkey(found[aid][0]):
                found[aid] = (ver, base)
    return found


def local_versions(aid):
    """Οι εκδόσεις που ήδη έχουμε κατεβασμένες, νεότερη πρώτη."""
    d = os.path.join(OUT_DIR, aid)
    if not os.path.isdir(d):
        return []
    vers = []
    for f in os.listdir(d):
        m = re.match(re.escape(aid) + r'-(.+)\.zip$', f)
        if m:
            vers.append(m.group(1))
    return sorted(vers, key=vkey, reverse=True)


def verify(blob, aid, ver):
    """Το zip πρέπει να ανοίγει και το addon.xml μέσα να λέει τη σωστή έκδοση."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        print('  ! δεν είναι έγκυρο zip')
        return None
    if zf.testzip() is not None:
        print('  ! χαλασμένο περιεχόμενο στο zip')
        return None
    name = '%s/addon.xml' % aid
    if name not in zf.namelist():
        print('  ! λείπει το %s μέσα από το zip' % name)
        return None
    root = parse_xml(zf.read(name), name)
    if root is None:
        return None
    if root.get('version') != ver:
        print('  ! ασυμφωνία έκδοσης: addons.xml λέει %s, addon.xml λέει %s'
              % (ver, root.get('version')))
        return None
    return root


def prune(aid):
    """Κρατάει μόνο τις KEEP νεότερες εκδόσεις."""
    d = os.path.join(OUT_DIR, aid)
    for old in local_versions(aid)[KEEP:]:
        f = os.path.join(d, '%s-%s.zip' % (aid, old))
        os.remove(f)
        print('  - διαγράφηκε παλιά έκδοση %s' % old)


def build_index():
    """Ξαναχτίζει το addons.xml + md5 από τα zips που υπάρχουν τοπικά."""
    entries = []
    for aid in sorted(os.listdir(OUT_DIR)):
        d = os.path.join(OUT_DIR, aid)
        if not os.path.isdir(d):
            continue
        vers = local_versions(aid)
        if not vers:
            continue
        zpath = os.path.join(d, '%s-%s.zip' % (aid, vers[0]))
        with zipfile.ZipFile(zpath) as zf:
            xml = zf.read('%s/addon.xml' % aid).decode('utf-8')
        xml = re.sub(r'^\s*<\?xml[^>]*\?>\s*', '', xml)
        entries.append(xml.strip())

    doc = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n%s\n</addons>\n' \
          % '\n'.join(entries)

    with open(os.path.join(OUT_DIR, 'addons.xml'), 'w', encoding='utf-8') as f:
        f.write(doc)
    md5 = hashlib.md5(doc.encode('utf-8')).hexdigest()
    with open(os.path.join(OUT_DIR, 'addons.xml.md5'), 'w', encoding='utf-8') as f:
        f.write(md5)
    print('addons.xml: %d addons, md5 %s' % (len(entries), md5))


# --- κυρίως ----------------------------------------------------------------

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    remote = remote_versions()

    missing = [a for a in WANTED if a not in remote]
    if missing:
        print('! δεν βρέθηκαν στο repo τους: %s' % ', '.join(missing))

    changed = False
    for aid, (ver, base) in sorted(remote.items()):
        have = local_versions(aid)
        if have and have[0] == ver:
            print('= %s %s (ίδιο, δεν κατεβαίνει)' % (aid, ver))
            continue

        url = '%s%s/%s-%s.zip' % (base, aid, aid, ver)
        print('> %s %s -> %s' % (aid, ver, url))
        try:
            blob = fetch(url)
        except Exception as e:
            print('  ! απέτυχε το κατέβασμα -> %s' % e)
            continue

        if verify(blob, aid, ver) is None:
            continue

        d = os.path.join(OUT_DIR, aid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, '%s-%s.zip' % (aid, ver)), 'wb') as f:
            f.write(blob)
        print('  + αποθηκεύτηκε')
        prune(aid)
        changed = True

    if changed:
        build_index()
    elif not os.path.exists(os.path.join(OUT_DIR, 'addons.xml')):
        build_index()
    else:
        print('καμία αλλαγή')

    return 0


if __name__ == '__main__':
    sys.exit(main())
