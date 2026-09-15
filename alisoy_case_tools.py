import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

VALID_CAPACITIES = {"vəkillik", "mmc"}


def _now() -> str:
    return datetime.utcnow().isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            contact TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS matters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id),
            capacity TEXT NOT NULL,
            practice_area TEXT,
            subject TEXT NOT NULL,
            responsible TEXT DEFAULT 'Cavid Əliyev',
            instansiya TEXT,
            opposing_party TEXT,
            status TEXT DEFAULT 'aktiv',
            next_step TEXT,
            next_step_date TEXT,
            valuation TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS deadlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            matter_id INTEGER NOT NULL REFERENCES matters(id),
            description TEXT NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT DEFAULT 'açıq',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def register_case_tools(mcp, data_root: str) -> None:
    """Registers Alisoy's client/matter/deadline/memory tools. Alisoy-specific (not tenant-agnostic) —
    call only for the alisoy tenant, from server.py."""

    db_path = Path(data_root).parent / "db" / "alisoy.db"
    _init_db(db_path)

    def _get_or_create_client(conn, name: str) -> int:
        row = conn.execute("SELECT id FROM clients WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO clients (name, created_at) VALUES (?, ?)", (name, _now())
        )
        return cur.lastrowid

    def _matter_row_to_dict(row) -> dict:
        return {
            "matter_id": row["id"],
            "client": row["client_name"],
            "capacity": row["capacity"],
            "practice_area": row["practice_area"],
            "subject": row["subject"],
            "responsible": row["responsible"],
            "instansiya": row["instansiya"],
            "opposing_party": row["opposing_party"],
            "status": row["status"],
            "next_step": row["next_step"],
            "next_step_date": row["next_step_date"],
            "valuation": row["valuation"],
        }

    # ---------- clients ----------

    def alisoy_add_client(name: str, contact: str = "", notes: str = "") -> dict:
        """Yeni müştəri əlavə edir (artıq varsa, mövcud müştərini qaytarır)."""
        conn = _connect(db_path)
        try:
            existing = conn.execute("SELECT id FROM clients WHERE name = ?", (name,)).fetchone()
            if existing:
                return {"status": "artıq mövcuddur", "client_id": existing["id"], "name": name}
            client_id = _get_or_create_client(conn, name)
            if contact or notes:
                conn.execute(
                    "UPDATE clients SET contact = ?, notes = ? WHERE id = ?",
                    (contact, notes, client_id),
                )
            conn.commit()
            return {"status": "əlavə edildi", "client_id": client_id, "name": name}
        finally:
            conn.close()

    def alisoy_list_clients(query: str = "") -> list[dict]:
        """Müştəri siyahısı, istəyə görə ada görə axtarışla süzülür."""
        conn = _connect(db_path)
        try:
            if query:
                rows = conn.execute(
                    "SELECT id, name, contact, notes FROM clients WHERE name LIKE ? ORDER BY name",
                    (f"%{query}%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, name, contact, notes FROM clients ORDER BY name"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def alisoy_client_summary(client: str) -> dict:
        """Bir müştərinin bütün işlərinin xülasəsi (ad və ya client_id ilə axtarıla bilər)."""
        conn = _connect(db_path)
        try:
            if client.isdigit():
                crow = conn.execute("SELECT * FROM clients WHERE id = ?", (int(client),)).fetchone()
            else:
                crow = conn.execute("SELECT * FROM clients WHERE name = ?", (client,)).fetchone()
            if not crow:
                return {"error": f"'{client}' adlı müştəri tapılmadı"}
            matters = conn.execute(
                """SELECT m.*, c.name AS client_name FROM matters m
                   JOIN clients c ON c.id = m.client_id
                   WHERE m.client_id = ? ORDER BY m.created_at DESC""",
                (crow["id"],),
            ).fetchall()
            return {
                "client": dict(crow),
                "matters": [_matter_row_to_dict(r) for r in matters],
            }
        finally:
            conn.close()

    def alisoy_conflict_check(names: list[str]) -> dict:
        """Verilmiş adları mövcud müştərilər və qarşı tərəflərlə (opposing_party) yoxlayır — maraqlar toqquşması riski üçün."""
        conn = _connect(db_path)
        try:
            hits = []
            for name in names:
                pattern = f"%{name}%"
                client_hits = conn.execute(
                    "SELECT id, name FROM clients WHERE name LIKE ?", (pattern,)
                ).fetchall()
                for ch in client_hits:
                    hits.append({"searched": name, "match_type": "müştəri", "match": ch["name"]})
                matter_hits = conn.execute(
                    """SELECT m.id, m.opposing_party, c.name AS client_name FROM matters m
                       JOIN clients c ON c.id = m.client_id
                       WHERE m.opposing_party LIKE ?""",
                    (pattern,),
                ).fetchall()
                for mh in matter_hits:
                    hits.append({
                        "searched": name,
                        "match_type": "qarşı tərəf",
                        "match": mh["opposing_party"],
                        "matter_id": mh["id"],
                        "existing_client": mh["client_name"],
                    })
            return {"conflict_risk": len(hits) > 0, "matches": hits}
        finally:
            conn.close()

    # ---------- matters ----------

    def alisoy_add_matter(
        client: str,
        capacity: str,
        subject: str,
        practice_area: str = "",
        instansiya: str = "",
        opposing_party: str = "",
        responsible: str = "Cavid Əliyev",
        valuation: str = "",
        status: str = "aktiv",
    ) -> dict:
        """Yeni iş əlavə edir. capacity: 'vəkillik' və ya 'mmc' olmalıdır."""
        if capacity not in VALID_CAPACITIES:
            return {"error": f"capacity 'vəkillik' və ya 'mmc' olmalıdır, alındı: '{capacity}'"}
        conn = _connect(db_path)
        try:
            client_id = _get_or_create_client(conn, client)
            now = _now()
            cur = conn.execute(
                """INSERT INTO matters
                   (client_id, capacity, practice_area, subject, responsible, instansiya,
                    opposing_party, status, valuation, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (client_id, capacity, practice_area, subject, responsible, instansiya,
                 opposing_party, status, valuation, now, now),
            )
            conn.commit()
            return {"status": "əlavə edildi", "matter_id": cur.lastrowid}
        finally:
            conn.close()

    def alisoy_list_matters(status: str = "", client: str = "", practice_area: str = "", capacity: str = "") -> list[dict]:
        """İşlərin siyahısı, ötürülən parametrlərə görə süzülür (hamısı istəyə bağlıdır)."""
        conn = _connect(db_path)
        try:
            query = """SELECT m.*, c.name AS client_name FROM matters m
                       JOIN clients c ON c.id = m.client_id WHERE 1=1"""
            params: list = []
            if status:
                query += " AND m.status = ?"
                params.append(status)
            if client:
                query += " AND c.name LIKE ?"
                params.append(f"%{client}%")
            if practice_area:
                query += " AND m.practice_area LIKE ?"
                params.append(f"%{practice_area}%")
            if capacity:
                query += " AND m.capacity = ?"
                params.append(capacity)
            query += " ORDER BY m.updated_at DESC"
            rows = conn.execute(query, params).fetchall()
            return [_matter_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def alisoy_matter_detail(matter_id: int) -> dict:
        """Bir işin tam detalı + ona bağlı bütün müddətlər."""
        conn = _connect(db_path)
        try:
            row = conn.execute(
                """SELECT m.*, c.name AS client_name FROM matters m
                   JOIN clients c ON c.id = m.client_id WHERE m.id = ?""",
                (matter_id,),
            ).fetchone()
            if not row:
                return {"error": f"#{matter_id} nömrəli iş tapılmadı"}
            deadlines = conn.execute(
                "SELECT id, description, due_date, status FROM deadlines WHERE matter_id = ? ORDER BY due_date",
                (matter_id,),
            ).fetchall()
            result = _matter_row_to_dict(row)
            result["deadlines"] = [dict(d) for d in deadlines]
            return result
        finally:
            conn.close()

    def alisoy_update_matter(
        matter_id: int,
        status: str = "",
        next_step: str = "",
        next_step_date: str = "",
        valuation: str = "",
        instansiya: str = "",
    ) -> dict:
        """Bir işin sahələrini yeniləyir (yalnız dolduraraq göndərdiyin sahələr dəyişir)."""
        conn = _connect(db_path)
        try:
            existing = conn.execute("SELECT id FROM matters WHERE id = ?", (matter_id,)).fetchone()
            if not existing:
                return {"error": f"#{matter_id} nömrəli iş tapılmadı"}
            fields = {
                "status": status, "next_step": next_step,
                "next_step_date": next_step_date, "valuation": valuation,
                "instansiya": instansiya,
            }
            updates = {k: v for k, v in fields.items() if v}
            if not updates:
                return {"status": "heç bir dəyişiklik göndərilmədi"}
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            params = list(updates.values()) + [_now(), matter_id]
            conn.execute(f"UPDATE matters SET {set_clause}, updated_at = ? WHERE id = ?", params)
            conn.commit()
            return {"status": "yeniləndi", "matter_id": matter_id, "updated_fields": list(updates.keys())}
        finally:
            conn.close()

    # ---------- deadlines ----------

    def alisoy_add_deadline(matter_id: int, description: str, due_date: str) -> dict:
        """Bir işə müddət/tarix əlavə edir. due_date formatı: YYYY-MM-DD."""
        conn = _connect(db_path)
        try:
            matter = conn.execute("SELECT id FROM matters WHERE id = ?", (matter_id,)).fetchone()
            if not matter:
                return {"error": f"#{matter_id} nömrəli iş tapılmadı"}
            cur = conn.execute(
                "INSERT INTO deadlines (matter_id, description, due_date, created_at) VALUES (?, ?, ?, ?)",
                (matter_id, description, due_date, _now()),
            )
            conn.commit()
            return {"status": "əlavə edildi", "deadline_id": cur.lastrowid}
        finally:
            conn.close()

    def alisoy_upcoming_deadlines(days: int = 7, capacity: str = "") -> list[dict]:
        """Növbəti <days> gün ərzində bitən açıq müddətlərin siyahısı (tarixə görə sıralı)."""
        conn = _connect(db_path)
        try:
            today = date.today().isoformat()
            until = (date.today() + timedelta(days=days)).isoformat()
            query = """SELECT d.id, d.description, d.due_date, m.id AS matter_id, m.subject,
                              m.capacity, c.name AS client_name
                       FROM deadlines d
                       JOIN matters m ON m.id = d.matter_id
                       JOIN clients c ON c.id = m.client_id
                       WHERE d.status = 'açıq' AND d.due_date BETWEEN ? AND ?"""
            params = [today, until]
            if capacity:
                query += " AND m.capacity = ?"
                params.append(capacity)
            query += " ORDER BY d.due_date"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ---------- memory ----------

    def alisoy_save_memory(topic: str, content: str) -> dict:
        """Vacib qərar/qeydi yaddaşa yazır (söhbətlər arası xatırlamaq üçün)."""
        conn = _connect(db_path)
        try:
            cur = conn.execute(
                "INSERT INTO memory (topic, content, created_at) VALUES (?, ?, ?)",
                (topic, content, _now()),
            )
            conn.commit()
            return {"status": "yadda saxlanıldı", "memory_id": cur.lastrowid}
        finally:
            conn.close()

    def alisoy_recall(query: str) -> list[dict]:
        """Yaddaşda mövzu və ya mətnə görə axtarış (ən yeni 10 nəticə)."""
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                """SELECT id, topic, content, created_at FROM memory
                   WHERE topic LIKE ? OR content LIKE ?
                   ORDER BY created_at DESC LIMIT 10""",
                (f"%{query}%", f"%{query}%"),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    for fn, name in [
        (alisoy_add_client, "alisoy_add_client"),
        (alisoy_list_clients, "alisoy_list_clients"),
        (alisoy_client_summary, "alisoy_client_summary"),
        (alisoy_conflict_check, "alisoy_conflict_check"),
        (alisoy_add_matter, "alisoy_add_matter"),
        (alisoy_list_matters, "alisoy_list_matters"),
        (alisoy_matter_detail, "alisoy_matter_detail"),
        (alisoy_update_matter, "alisoy_update_matter"),
        (alisoy_add_deadline, "alisoy_add_deadline"),
        (alisoy_upcoming_deadlines, "alisoy_upcoming_deadlines"),
        (alisoy_save_memory, "alisoy_save_memory"),
        (alisoy_recall, "alisoy_recall"),
    ]:
        mcp.tool(name=name)(fn)
