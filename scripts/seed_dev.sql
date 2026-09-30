-- Development seed (idempotent) untuk integrasi frontend lokal.
-- Jalankan: docker compose --env-file .env --env-file .env.deploy exec -T postgres \
--   psql -U temanbule -d temanbule -f /path/to/seed_dev.sql
--
-- CATATAN: nilai di bawah adalah fixture DEVELOPMENT eksplisit, bukan konten
-- produksi. Plans/policy/agents/categories sudah di-seed terpisah pada sesi
-- penyiapan awal. File ini menyediakan konten published agar Home/Learn dan
-- TOEFL dapat diverifikasi dari UI.

-- ===== Catalog (plans + published policy) =====
INSERT INTO plans (code, policy_version, status)
VALUES ('vip', NULL, 'active'), ('advance', NULL, 'active')
ON CONFLICT (code) DO UPDATE SET status = EXCLUDED.status;

INSERT INTO plan_policy_versions (id, plan_code, revision, policy, status)
VALUES
  ('01M3DEVPOLICYVIP0000000001', 'vip', 1, '{"plan":"vip","payer":"wallet"}', 'published'),
  ('01M3DEVPOLICYADV0000000001', 'advance', 1, '{"plan":"advance","payer":"byok"}', 'published')
ON CONFLICT (plan_code, revision) DO NOTHING;

UPDATE plans SET policy_version = 1 WHERE code IN ('vip','advance');

-- ===== Practice categories (seed set dari product-requirements.md) =====
-- image_key menunjuk ke object di bucket S3/MinIO (folder "categories/").
INSERT INTO practice_categories (id, code, title, description, image_key, status, sort_order) VALUES
  ('01M3DEVCATDAILY00000000001', 'daily_conversation', 'Daily Conversation', 'Praktik percakapan sehari-hari: sapaan, small talk, dan situasi umum.', 'categories/daily_conversation.jpeg', 'published', 1),
  ('01M3DEVCATGRAMMAR000000001', 'grammar', 'Grammar', 'Latihan tata bahasa Inggris lewat contoh nyata dan koreksi langsung.', 'categories/grammar.jpeg', 'published', 2),
  ('01M3DEVCATPRONOUN000000001', 'pronunciation', 'Pronunciation', 'Perbaiki pelafalan dan intonasi agar terdengar lebih natural.', 'categories/pronunciation.jpeg', 'published', 3),
  ('01M3DEVCATJOBINT0000000001', 'job_interview', 'Job Interview', 'Simulasi wawancara kerja bahasa Inggris dengan umpan balik terarah.', 'categories/job_interview.jpeg', 'published', 4),
  ('01M3DEVCATTRAVEL0000000001', 'travel', 'Travel', 'Percakapan praktis untuk perjalanan: bandara, hotel, dan restoran.', 'categories/travel.jpeg', 'published', 5),
  ('01M3DEVCATFREETALK00000001', 'free_talk', 'Free Talk', 'Ngobrol bebas tentang topik apa pun untuk membangun kelancaran.', 'categories/free_talk.jpeg', 'published', 6)
ON CONFLICT (code) DO UPDATE SET
  description = EXCLUDED.description,
  image_key = EXCLUDED.image_key;

-- ===== Agents Elean & Willy + published versions =====
INSERT INTO agents (id, code, display_name, status, active_version_id)
VALUES
  ('01M3DEVAGENTELEAN00000001', 'elean', 'Elean', 'active', NULL),
  ('01M3DEVAGENTWILLY00000001', 'willy', 'Willy', 'active', NULL)
ON CONFLICT (code) DO UPDATE SET status = EXCLUDED.status;

INSERT INTO agent_versions (id, agent_id, revision, status)
VALUES
  ('01M3DEVAGVERELEAN00000001', '01M3DEVAGENTELEAN00000001', 1, 'published'),
  ('01M3DEVAGVERWILLY00000001', '01M3DEVAGENTWILLY00000001', 1, 'published')
ON CONFLICT (agent_id, revision) DO NOTHING;

UPDATE agents SET active_version_id = '01M3DEVAGVERELEAN00000001' WHERE code = 'elean';
UPDATE agents SET active_version_id = '01M3DEVAGVERWILLY00000001' WHERE code = 'willy';

-- ===== Learning content (published course -> unit -> lesson -> content version) =====
INSERT INTO courses (id, slug, title, level, status) VALUES
  ('01M3DEVCOURSE00000000001', 'english-conversation-b1', 'English Conversation B1', 'B1', 'published')
ON CONFLICT (slug) DO UPDATE SET status = EXCLUDED.status;

INSERT INTO course_units (id, course_id, title, position) VALUES
  ('01M3DEVUNIT0000000000001', '01M3DEVCOURSE00000000001', 'Unit 1: Daily Conversations', 1)
ON CONFLICT (course_id, position) DO NOTHING;

INSERT INTO lessons (id, unit_id, slug, title, level, position, status) VALUES
  ('01M3DEVLESSON00000000001', '01M3DEVUNIT0000000000001', 'greetings-and-introductions', 'Greetings and Introductions', 'B1', 1, 'published')
ON CONFLICT (unit_id, position) DO NOTHING;

INSERT INTO learning_content_versions (id, lesson_id, revision, content_type, body, publication_state, published_at) VALUES
  ('01M3DEVLCV00000000000001', '01M3DEVLESSON00000000001', 1, 'reading',
   'In this lesson you will practise common English greetings and how to introduce yourself.\n\nExamples:\n- "Good morning, how are you today?"\n- "Nice to meet you. My name is Budi."\n- "Where are you from?"\n\nTry to answer in full sentences.',
   'published', now())
ON CONFLICT (lesson_id, revision) DO NOTHING;

-- ===== TOEFL test version (published) =====
INSERT INTO toefl_test_versions (id, code, revision, rubric_version, definition, publication_state) VALUES
  ('01M3DEVTOEFL000000000001', 'toefl-itp-sim-1', 1, 'rubric-v1',
   'TOEFL ITP Simulation 1 (practice only, not an official score). Sections: reading, listening, structure & written expression.',
   'published')
ON CONFLICT (code, revision) DO NOTHING;
