"""Ordered candidate streams preserve complete lookup with bounded SQL work."""
from app import ingest
from test_compression import library


def test_ordered_merge_avoids_repeated_whole_population_sorts(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    count = 7500
    with db.lock:
        db.conn.executemany('INSERT INTO images(id,file,orig_name,object_name,ingested_at,'
            'content_sha256,file_size) VALUES (?,?,?,?,?,?,?)',
            ((i, f'synthetic/{i}.fits', f'{i}.fits', 'Metadata probe', '2026-10-17',
              f'{i:064x}', None if i % 17 == 0 else 5760 if i % 4 else 11520)
             for i in range(1, count + 1)))
        db.conn.commit()
    digest, size = f'{1:064x}', 5760  # Overlap the SHA and size tiers.
    def former_pages():
        last = 0
        while True:
            rows = db.conn.execute('SELECT id,file,content_sha256,file_size,file_mtime_ns '
                'FROM images WHERE (content_sha256=? OR file_size=? OR file_size IS NULL) '
                'AND id<=? AND id>? ORDER BY id LIMIT ?',
                (digest, size, count, last, ingest._CANDIDATE_PAGE_SIZE)).fetchall()
            yield from rows
            if len(rows) < ingest._CANDIDATE_PAGE_SIZE:
                return
            last = rows[-1]['id']
    def observe(function):
        ticks = []
        db.conn.set_progress_handler(lambda: ticks.append(1) or 0, 1000)
        try:
            ids = [row['id'] for row in function()]
        finally:
            db.conn.set_progress_handler(None, 0)
        return ids, len(ticks)
    try:
        before, former = observe(former_pages)
        after, merged = observe(lambda: ingest._batch_candidates(db, digest, size))
        assert before == after and len(after) > 4096 and len(after) == len(set(after))
        # Count executed SQLite VM instructions, not wall-clock speed. A broad
        # factor leaves room for SQLite versions while catching repeated sorts.
        assert merged < former / 2, (merged, former)
    finally:
        db.conn.close()


def test_alias_pages_merge_overlap_and_keep_initial_upper_boundary(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    monkeypatch.setattr(ingest, '_CANDIDATE_PAGE_SIZE', 2)
    digest, size = 'a' * 64, 5760
    def add(number):
        image = db.add_image(dict(file=f'probe/{number}.fits', orig_name=f'{number}.fits',
            object_name='Alias probe', ingested_at='2026-10-17', file_size=size,
            content_sha256='b' * 64))
        with db.lock:
            db.conn.execute('INSERT INTO image_compression VALUES(?,?,?,?,?,?,?)',
                (image, digest if number % 2 else 'c' * 64, size, f'{number}.fits',
                 digest, size, 'b' * 64))
            db.conn.commit()
        return image
    try:
        expected = [add(number) for number in range(1, 7)]
        candidates = ingest._alias_candidates(db, digest, size)
        first = next(candidates)
        assert not db.lock._is_owned()
        add(7)
        found = [first, *candidates]
        assert [row['id'] for row in found] == expected
        assert all(row['mapped_sha256'] == 'b' * 64 for row in found)
    finally:
        db.conn.close()
