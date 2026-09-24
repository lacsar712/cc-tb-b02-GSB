import os
import time

import psycopg2

from rules import weigh


def connect():
    last = None
    for _ in range(30):
        try:
            return psycopg2.connect(os.environ["DATABASE_URL"])
        except psycopg2.OperationalError as exc:
            last = exc
            time.sleep(1)
    raise last


def main():
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS cuppings (
            id serial PRIMARY KEY,
            lot text NOT NULL,
            aroma double precision NOT NULL,
            taste double precision NOT NULL,
            liquor double precision NOT NULL,
            score double precision NOT NULL,
            verdict text NOT NULL,
            note text NOT NULL,
            created_by text NOT NULL
        )"""
    )

    # 审评台名册：台名唯一
    cur.execute(
        """CREATE TABLE IF NOT EXISTS stations (
            name text PRIMARY KEY,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )"""
    )

    # 调拨履历：只写不改
    cur.execute(
        """CREATE TABLE IF NOT EXISTS transfers (
            id serial PRIMARY KEY,
            source_station text NOT NULL REFERENCES stations(name),
            target_station text NOT NULL REFERENCES stations(name),
            lot text NOT NULL,
            moved_by text NOT NULL,
            moved_at timestamptz NOT NULL DEFAULT now()
        )"""
    )
    cur.execute(
        """CREATE OR REPLACE FUNCTION transfers_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '调拨履历不可删改';
        END;
        $$ LANGUAGE plpgsql"""
    )
    cur.execute("DROP TRIGGER IF EXISTS transfers_no_update ON transfers")
    cur.execute(
        """CREATE TRIGGER transfers_no_update BEFORE UPDATE ON transfers
           FOR EACH ROW EXECUTE FUNCTION transfers_block_mutation()"""
    )
    cur.execute("DROP TRIGGER IF EXISTS transfers_no_delete ON transfers")
    cur.execute(
        """CREATE TRIGGER transfers_no_delete BEFORE DELETE ON transfers
           FOR EACH ROW EXECUTE FUNCTION transfers_block_mutation()"""
    )

    cur.execute("SELECT COUNT(*) FROM cuppings")
    if cur.fetchone()[0] == 0:
        for lot, aroma, taste, liquor in (("春茶-A", 8, 8, 7), ("夏茶-C", 5, 4, 6)):
            verdict, note, score = weigh(aroma, taste, liquor)
            cur.execute(
                """INSERT INTO cuppings (lot, aroma, taste, liquor, score, verdict, note, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (lot, aroma, taste, liquor, score, verdict, note, "taster"),
            )
    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
