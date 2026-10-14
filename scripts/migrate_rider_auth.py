import os
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from app.db import engine, SessionLocal
from sqlalchemy import text
from app.utils.auth import hash_password

def run_migration():
    print("Running Rider Auth & Session migration...")
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. Add password_hash column to delivery_partners if not exists
            conn.execute(text("""
                ALTER TABLE delivery_partners 
                ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);
            """))
            print("[OK] Checked/added 'password_hash' column to delivery_partners.")

            # 2. Create rider_sessions table if not exists for token revocation / session tracking
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS rider_sessions (
                    id BIGSERIAL PRIMARY KEY,
                    rider_id BIGINT NOT NULL REFERENCES delivery_partners(id) ON DELETE CASCADE,
                    jti VARCHAR(64) NOT NULL UNIQUE,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
                    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    is_revoked BOOLEAN DEFAULT FALSE NOT NULL
                );
            """))
            print("[OK] Checked/created 'rider_sessions' table.")

            # 3. Create indexes for performance and security
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_rider_sessions_jti ON rider_sessions(jti);
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_rider_sessions_rider_id ON rider_sessions(rider_id);
            """))
            print("[OK] Checked/created indexes on rider_sessions.")

            trans.commit()
            print("Migration committed successfully.")
        except Exception as e:
            trans.rollback()
            print(f"Migration error: {e}")
            raise e

def provision_sample_rider_passwords():
    """
    Sets distinct secure individual passwords for test riders if unset.
    Rider 1: Rider@1234
    Rider 2: Rider@3210
    Rider 3: Rider@4210
    Rider 4: Rider@5678
    """
    s = SessionLocal()
    try:
        sample_passwords = {
            1: "Rider@1234",
            2: "Rider@3210",
            3: "Rider@4210",
            4: "Rider@5678"
        }
        for rider_id, pwd in sample_passwords.items():
            current = s.execute(text("SELECT password_hash FROM delivery_partners WHERE id = :id"), {"id": rider_id}).fetchone()
            if current and not current[0]:
                hashed = hash_password(pwd)
                s.execute(
                    text("UPDATE delivery_partners SET password_hash = :hash WHERE id = :id"),
                    {"hash": hashed, "id": rider_id}
                )
                print(f"[OK] Provisioned initial password for rider ID {rider_id}")
        s.commit()
    finally:
        s.close()

if __name__ == "__main__":
    run_migration()
    provision_sample_rider_passwords()
