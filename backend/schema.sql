PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL,
  password TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('municipality','driver','neighbor')),
  district TEXT NOT NULL,
  UNIQUE(id, district)
);
CREATE TABLE IF NOT EXISTS sessions (
  digest TEXT PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trucks (
  id INTEGER PRIMARY KEY,
  district TEXT NOT NULL,
  code TEXT NOT NULL COLLATE NOCASE,
  plate TEXT COLLATE NOCASE,
  name TEXT NOT NULL,
  driver_id INTEGER,
  active INTEGER NOT NULL CHECK(active IN (0,1)),
  condition TEXT NOT NULL CHECK(condition IN ('available','maintenance')),
  sample INTEGER NOT NULL DEFAULT 0 CHECK(sample IN (0,1)),
  UNIQUE(district, code), UNIQUE(district, plate), UNIQUE(id, district),
  FOREIGN KEY(driver_id,district) REFERENCES accounts(id,district)
);
CREATE TABLE IF NOT EXISTS routes (
  id INTEGER PRIMARY KEY,
  district TEXT NOT NULL,
  name TEXT NOT NULL,
  streets TEXT NOT NULL,
  sectors TEXT NOT NULL,
  truck_id INTEGER NOT NULL,
  driver_id INTEGER NOT NULL,
  days TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  points TEXT NOT NULL,
  active INTEGER NOT NULL CHECK(active IN (0,1)),
  archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
  UNIQUE(id,district),
  FOREIGN KEY(truck_id,district) REFERENCES trucks(id,district),
  FOREIGN KEY(driver_id,district) REFERENCES accounts(id,district)
);
CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY,
  district TEXT NOT NULL,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  district TEXT NOT NULL,
  route_id INTEGER NOT NULL,
  truck_id INTEGER NOT NULL,
  driver_id INTEGER NOT NULL,
  service_date TEXT NOT NULL,
  phase TEXT NOT NULL CHECK(phase IN ('running','paused','finished')),
  UNIQUE(route_id, service_date),
  FOREIGN KEY(route_id,district) REFERENCES routes(id,district),
  FOREIGN KEY(truck_id,district) REFERENCES trucks(id,district),
  FOREIGN KEY(driver_id,district) REFERENCES accounts(id,district)
);
CREATE UNIQUE INDEX IF NOT EXISTS truck_in_service ON runs(truck_id) WHERE phase <> 'finished';
CREATE UNIQUE INDEX IF NOT EXISTS driver_in_service ON runs(driver_id) WHERE phase <> 'finished';
CREATE TABLE IF NOT EXISTS attended (
  run_id INTEGER NOT NULL REFERENCES runs(id),
  street TEXT NOT NULL,
  PRIMARY KEY(run_id,street)
);
CREATE INDEX IF NOT EXISTS routes_district ON routes(district,archived,active);
CREATE INDEX IF NOT EXISTS announcements_district ON announcements(district,published_at);

CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY,
  district TEXT NOT NULL,
  route_id INTEGER NOT NULL,
  driver_id INTEGER NOT NULL,
  category TEXT NOT NULL,
  message TEXT NOT NULL,
  reported_at TEXT NOT NULL,
  FOREIGN KEY(route_id,district) REFERENCES routes(id,district),
  FOREIGN KEY(driver_id,district) REFERENCES accounts(id,district)
);
CREATE INDEX IF NOT EXISTS incidents_district_order ON incidents(district,id DESC);
