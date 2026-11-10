"""V1 canonical account resolver. No commits or network access."""
import re

SUFFIX = re.compile(
    r"-vless_reality_(?:grpc|vision)$", re.I
)
PERSON = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")


def resolve(db, source_id, raw_identity, trusted_prefixes):
    """Return (account_id, status). Caller owns the transaction."""
    if not isinstance(raw_identity, str):
        raise ValueError("Invalid raw identity")

    raw_identity = raw_identity.strip()
    if not raw_identity or len(raw_identity) > 512:
        raise ValueError("Invalid raw identity")

    source = db.execute(
        "SELECT enabled FROM sources WHERE source_id=?",
        (source_id,)
    ).fetchone()

    if source is None or source[0] != 1:
        raise ValueError("Unknown or disabled source")

    existing = db.execute("""
        SELECT account_id,mapping_status
        FROM source_accounts
        WHERE source_id=? AND raw_identity=?
    """, (source_id, raw_identity)).fetchone()

    if existing is not None:
        return existing

    clean = SUFFIX.sub(
        "", raw_identity.replace("，", ",")
    ).strip()

    account_id = None

    # Explicit aliases take priority over inferred names.
    for alias in (raw_identity, clean):
        row = db.execute("""
            SELECT account_id FROM account_aliases
            WHERE alias=? COLLATE NOCASE
        """, (alias,)).fetchone()
        if row:
            account_id = row[0]
            break

    # Infer only from configured, trusted source prefixes.
    if account_id is None and "," in clean:
        prefix, person = clean.rsplit(",", 1)
        prefix = prefix.strip()
        person = person.strip()

        permitted = {
            str(p).casefold() for p in trusted_prefixes
        }

        if (
            prefix.casefold() in permitted
            and PERSON.fullmatch(person)
        ):
            row = db.execute("""
                SELECT account_id FROM accounts
                WHERE canonical_name=? COLLATE NOCASE
            """, (person,)).fetchone()

            if row:
                account_id = row[0]
            else:
                account_id = "acc_" + person.casefold()
                db.execute("""
                    INSERT INTO accounts(
                        account_id,canonical_name,status
                    ) VALUES (?,?,'active')
                """, (account_id, person))
                db.execute("""
                    INSERT INTO account_aliases(
                        alias,account_id,kind
                    ) VALUES (?,?,'canonical')
                """, (person, account_id))

    status = "mapped" if account_id else "pending"

    db.execute("""
        INSERT INTO source_accounts(
            source_id,raw_identity,account_id,mapping_status
        ) VALUES (?,?,?,?)
    """, (source_id, raw_identity, account_id, status))

    return account_id, status
