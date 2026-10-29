-- Sample data for a FICTIONAL wholesale distributor: "Cedar Ridge Supply Co."
-- (foodservice packaging and janitorial supplies). Every company, person and
-- address below is made up. Email domains use the reserved ".example" TLD.
-- Dates are relative to the day the database is created, so the demo always
-- has the same mix of active and quiet customers.

INSERT INTO accounts (company, contact_name, email, kind, segment, city, state, sales_rep, source, notes, do_not_contact) VALUES
-- customers that still order every few weeks (should NOT get a follow-up)
('Maple Street Cafe',        'Hannah Brooks',  'hannah@maplestreetcafe.example',   'customer', 'Cafe & bakery',     'Columbus',   'OH', 'Maria',  NULL, NULL, false),
('Riverside Grill',          'Tom Alvarez',    'tom@riversidegrill.example',       'customer', 'Restaurant',        'Dayton',     'OH', 'Jordan', NULL, NULL, false),
('Lakeview Dental Group',    'Priya Nair',     'office@lakeviewdental.example',    'customer', 'Healthcare office', 'Toledo',     'OH', 'Maria',  NULL, NULL, false),
('Summit Fitness Club',      'Greg Olsen',     'greg@summitfitness.example',       'customer', 'Gym & fitness',     'Pittsburgh', 'PA', 'Jordan', NULL, NULL, false),
('Golden Wok Kitchen',       'Lily Chen',      'lily@goldenwok.example',           'customer', 'Restaurant',        'Cleveland',  'OH', 'Jordan', NULL, NULL, false),
-- customers that went quiet (should get a reconnect draft)
('Harbor View Deli',         'Sam Patel',      'sam@harborviewdeli.example',       'customer', 'Cafe & bakery',     'Cleveland',  'OH', 'Maria',  NULL, 'Asked about compostable lids last spring.', false),
('Pine Hollow Bakery',       'Erin Walsh',     'erin@pinehollowbakery.example',    'customer', 'Cafe & bakery',     'Akron',      'OH', 'Maria',  NULL, NULL, false),
('Northgate Office Park',    'Dev Raman',      'facilities@northgatepark.example', 'customer', 'Property management','Erie',      'PA', 'Jordan', NULL, 'Buys janitorial supplies for 4 buildings.', false),
('Bluefin Bistro',           'Carla Diaz',     'purchasing@@bluefinbistro.example','customer', 'Restaurant',        'Ann Arbor',  'MI', 'Jordan', NULL, 'Email address was typed wrong at signup.', false),
('Copper Kettle Diner',      'Mike Sorensen',  'mike@copperkettle.example',        'customer', 'Restaurant',        'Canton',     'OH', 'Jordan', NULL, NULL, false),
-- quiet, but asked not to be contacted (must be skipped)
('Old Mill Tavern',          'Rita Kowalski',  'rita@oldmilltavern.example',       'customer', 'Restaurant',        'Youngstown', 'OH', 'Maria',  NULL, 'Asked us to stop sales emails.', true),
-- prospects (new wholesale leads, e.g. from a trade-show list)
('Fresh Fork Food Truck',    'Andre Lewis',    'andre@freshfork.example',          'prospect', 'Restaurant',        'Columbus',   'OH', 'Maria',  'Trade show list', NULL, false),
('Sunrise Senior Living',    'Beth Moran',     'purchasing@sunrisesenior.example', 'prospect', 'Healthcare office', 'Lansing',    'MI', 'Jordan', 'Website contact form', NULL, false),
('Corner Bean Coffee',       'Nina Park',      'nina@cornerbean.example',          'prospect', 'Cafe & bakery',     'Dayton',     'OH', 'Maria',  'Trade show list', NULL, false),
('Westside Self Storage',    'Paul Grant',     'paul@westsidestorage.example',     'prospect', 'Property management','Buffalo',   'NY', 'Jordan', 'Purchased lead list', NULL, false),
('Hilltop Brewing Co-op',    'Jess Moreau',    'jess@hilltopbrewing.example',      'prospect', 'Restaurant',        'Pittsburgh', 'PA', 'Jordan', 'Referral', NULL, false);

-- Orders. "d" = days before today.
INSERT INTO orders (account_id, order_date, amount, main_product)
SELECT a.id, current_date - v.d, v.amount, v.product
FROM (VALUES
    ('Maple Street Cafe',     12,  840.00, '12 oz hot cups and lids'),
    ('Maple Street Cafe',     40,  910.00, '12 oz hot cups and lids'),
    ('Riverside Grill',        8, 1320.00, 'take-out containers'),
    ('Riverside Grill',       35, 1180.00, 'take-out containers'),
    ('Lakeview Dental Group', 20,  460.00, 'nitrile gloves'),
    ('Summit Fitness Club',   15,  690.00, 'paper towels and sanitizer'),
    ('Golden Wok Kitchen',    25, 1550.00, 'fold-top food pails'),
    ('Golden Wok Kitchen',    55, 1490.00, 'fold-top food pails'),
    ('Harbor View Deli',      74,  780.00, 'deli containers'),
    ('Harbor View Deli',     110,  820.00, 'deli containers'),
    ('Pine Hollow Bakery',    96,  540.00, 'bakery boxes'),
    ('Northgate Office Park', 128, 2210.00, 'trash liners and floor cleaner'),
    ('Bluefin Bistro',        88,  960.00, 'take-out containers'),
    ('Copper Kettle Diner',   67,  610.00, 'napkins and placemats'),
    ('Old Mill Tavern',      150,  430.00, 'bar napkins')
) AS v(company, d, amount, product)
JOIN accounts a ON a.company = v.company;
