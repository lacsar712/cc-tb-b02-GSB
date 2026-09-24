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
    cur.execute(
        """CREATE TABLE IF NOT EXISTS cupping_tables (
            id serial PRIMARY KEY,
            name text NOT NULL UNIQUE,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS transfers (
            id serial PRIMARY KEY,
            lot text NOT NULL,
            source_table text NOT NULL REFERENCES cupping_tables (name),
            target_table text NOT NULL REFERENCES cupping_tables (name),
            moved_by text NOT NULL,
            moved_at timestamptz NOT NULL DEFAULT now()
        )"""
    )
    cur.execute(
        """CREATE OR REPLACE FUNCTION transfers_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '调拨履历不可删改';
        END;
        $$ LANGUAGE plpgsql"""
    )
    cur.execute("DROP TRIGGER IF EXISTS transfers_no_change ON transfers")
    cur.execute(
        """CREATE TRIGGER transfers_no_change
           BEFORE UPDATE OR DELETE ON transfers
           FOR EACH ROW EXECUTE FUNCTION transfers_immutable()"""
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
