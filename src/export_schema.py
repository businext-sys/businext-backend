# Dumps the SQLite schema to esquema.sql
import sqlite3

conn = sqlite3.connect("database.db")
with open("esquema.sql", "w", encoding="utf-8") as f:
    for line in conn.iterdump():
        if line.startswith("CREATE TABLE"):
            f.write(line + "\n")
conn.close()
