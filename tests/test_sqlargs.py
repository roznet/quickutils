import sqlite3

from bin import sqlargs


def _make_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE airports (code TEXT, name TEXT, country TEXT, extra INTEGER)"
        )
        cur.executemany(
            "INSERT INTO airports (code, name, country, extra) VALUES (?, ?, ?, ?)",
            [
                ("AAA", "Alpha", "US", 1),
                ("BBB", "Bravo", "US", 2),
                ("CCC", "Charlie", "FR", 3),
                ("DDD", None, "FR", 4),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return str(db_path)


def test_build_args_default_column_and_separator(tmp_path):
    db_path = _make_db(tmp_path)

    result = sqlargs.build_args(
        db_path,
        "SELECT code FROM airports WHERE country = 'US' ORDER BY code",
    )

    assert result == "AAA,BBB"


def test_build_args_column_by_name_and_space_separator(tmp_path):
    db_path = _make_db(tmp_path)

    result = sqlargs.build_args(
        db_path,
        "SELECT code, name FROM airports WHERE country = 'US' ORDER BY code",
        column="name",
        separator=" ",
    )

    assert result == "Alpha Bravo"


def test_build_args_unique_and_null_replacement(tmp_path):
    db_path = _make_db(tmp_path)

    result = sqlargs.build_args(
        db_path,
        "SELECT name FROM airports WHERE country = 'FR' ORDER BY code",
        unique=True,
        null_replacement="<none>",
        separator=";",
    )

    assert result == "Charlie;<none>"


def test_build_args_column_by_index(tmp_path):
    db_path = _make_db(tmp_path)

    result = sqlargs.build_args(
        db_path,
        "SELECT code, country FROM airports ORDER BY code",
        column="1",
        separator="+",
    )

    # order AAA, BBB, CCC, DDD
    assert result == "US+US+FR+FR"


def test_build_args_shell_quote(tmp_path):
    db_path = _make_db(tmp_path)

    result = sqlargs.build_args(
        db_path,
        "SELECT name FROM airports WHERE code = 'AAA'",
        quote="shell",
    )

    # simple value, just single quoted
    assert result == "'Alpha'"


def test_build_args_csv_quote(tmp_path):
    db_path = _make_db(tmp_path)

    result = sqlargs.build_args(
        db_path,
        "SELECT name FROM airports WHERE code = 'AAA'",
        quote="csv",
    )

    # simple value, double quoted
    assert result == '"Alpha"'


def test_build_args_csv_quote_with_double_quote(tmp_path):
    db_path = _make_db(tmp_path)
    
    # Add a row with a name containing a double quote
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO airports (code, name, country) VALUES (?, ?, ?)", 
                   ("EEE", 'Test "Quote" Value', "US"))
        conn.commit()
    finally:
        conn.close()

    result = sqlargs.build_args(
        db_path,
        "SELECT name FROM airports WHERE code = 'EEE'",
        quote="csv",
    )

    # double quotes should be escaped by doubling them
    assert result == '"Test ""Quote"" Value"'


def test_main_invocation_creates_output(tmp_path, capsys):
    db_path = _make_db(tmp_path)

    exit_code = sqlargs.main(
        [
            db_path,
            "SELECT code FROM airports WHERE country = 'FR' ORDER BY code",
            "--space",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    # Two FR airports CCC and DDD
    assert captured.out.strip() == "CCC DDD"


