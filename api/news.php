<?php
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST');
header('Access-Control-Allow-Headers: X-API-Key, Content-Type');

require_once __DIR__ . '/config.php';

$method = $_SERVER['REQUEST_METHOD'];

try {
    $pdo = new PDO(
        "mysql:host=" . DB_HOST . ";dbname=" . DB_NAME . ";charset=utf8mb4",
        DB_USER,
        DB_PASS,
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
    );
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode(['error' => 'Database connection failed', 'detail' => $e->getMessage()]);
    exit;
}

$pdo->exec("
    CREATE TABLE IF NOT EXISTS articles (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        title           VARCHAR(1000) NOT NULL,
        source          VARCHAR(200),
        url             VARCHAR(2000) NOT NULL,
        content         TEXT,
        summary         TEXT,
        image_url       VARCHAR(2000),
        timestamp       DATETIME,
        category        VARCHAR(50),
        rally_originals TINYINT(1) NOT NULL DEFAULT 0,
        writing_style   JSON NULL,
        complexity      VARCHAR(20),
        topics          JSON NULL,
        countries       JSON NULL,
        people          JSON NULL,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_url (url(767))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
");

// Backfill columns on tables created before this metadata was added.
foreach ([
    'writing_style' => "ALTER TABLE articles ADD COLUMN writing_style JSON NULL",
    'complexity'    => "ALTER TABLE articles ADD COLUMN complexity VARCHAR(20)",
    'topics'        => "ALTER TABLE articles ADD COLUMN topics JSON NULL",
    'countries'     => "ALTER TABLE articles ADD COLUMN countries JSON NULL",
    'people'        => "ALTER TABLE articles ADD COLUMN people JSON NULL",
] as $sql) {
    try {
        $pdo->exec($sql);
    } catch (PDOException $e) {
        // Column already exists
    }
}

if ($method === 'GET') {
    $limit  = min((int)($_GET['limit']  ?? 200), 500);
    $offset = (int)($_GET['offset'] ?? 0);
    $category = $_GET['category'] ?? null;

    $columns = "title, source, url, content, summary, image_url, timestamp, category, rally_originals,
                writing_style, complexity, topics, countries, people";

    if ($category) {
        $stmt = $pdo->prepare("
            SELECT $columns
            FROM articles WHERE category = ?
            ORDER BY timestamp DESC LIMIT $limit OFFSET $offset
        ");
        $stmt->execute([$category]);
    } else {
        $stmt = $pdo->query("
            SELECT $columns
            FROM articles
            ORDER BY timestamp DESC LIMIT $limit OFFSET $offset
        ");
    }

    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    foreach ($rows as &$row) {
        foreach (['writing_style', 'topics', 'countries', 'people'] as $field) {
            $row[$field] = $row[$field] !== null ? json_decode($row[$field], true) : [];
        }
    }
    echo json_encode($rows);

} elseif ($method === 'POST') {
    $provided_key = $_SERVER['HTTP_X_API_KEY'] ?? '';
    if ($provided_key !== API_KEY) {
        http_response_code(401);
        echo json_encode(['error' => 'Unauthorized']);
        exit;
    }

    $data = json_decode(file_get_contents('php://input'), true);
    if (!$data) {
        http_response_code(400);
        echo json_encode(['error' => 'Invalid JSON']);
        exit;
    }

    // Accept either a single article object or an array of articles
    $articles = isset($data[0]) ? $data : [$data];
    $inserted = 0;

    $stmt = $pdo->prepare("
        INSERT IGNORE INTO articles
            (title, source, url, content, summary, image_url, timestamp, category, rally_originals,
             writing_style, complexity, topics, countries, people)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
    ");

    foreach ($articles as $article) {
        $stmt->execute([
            $article['title']     ?? '',
            $article['source']    ?? '',
            $article['url']       ?? '',
            $article['content']   ?? $article['first_paragraph'] ?? '',
            $article['summary']   ?? '',
            $article['image_url'] ?? '',
            $article['timestamp'] ?? date('Y-m-d H:i:s'),
            $article['category']  ?? 'world',
            json_encode($article['writing_style'] ?? []),
            $article['complexity'] ?? 'Moderate',
            json_encode($article['topics'] ?? []),
            json_encode($article['countries'] ?? []),
            json_encode($article['people'] ?? []),
        ]);
        if ($stmt->rowCount() > 0) $inserted++;
    }

    echo json_encode(['success' => true, 'inserted' => $inserted]);

} else {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed']);
}
