-- Removes benchmark fixtures and any analytics they produced (aggregate rows cascade with the URL).
DELETE FROM urls WHERE short_code LIKE 'bh_____' OR short_code LIKE 'bc_____';
DELETE FROM click_events WHERE short_code LIKE 'bh_____' OR short_code LIKE 'bc_____';
