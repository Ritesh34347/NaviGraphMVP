-- Enable extensions NaviGraph's application services rely on.
-- pgcrypto: cryptographic functions (used for hashing, token generation).
-- uuid-ossp: UUID generation functions, used for primary keys across
-- tenant-scoped tables.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
