.bail on
SELECT 'SQLITE_TEST_HELLO: Hello from SQLite';
CREATE TABLE nanvix_test(id INTEGER PRIMARY KEY, name TEXT);
INSERT INTO nanvix_test VALUES(1, 'nanvix');
INSERT INTO nanvix_test VALUES(2, 'python');
SELECT 'SQLITE_TEST_COUNT:', count(*) FROM nanvix_test;
SELECT 'SQLITE_TEST_ROW:', id, name FROM nanvix_test WHERE id=1;
