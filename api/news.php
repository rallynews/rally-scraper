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
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_url (url(767))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
");

if ($method === 'GET') {
    $limit  = min((int)($_GET['limit']  ?? 200), 500);
    $offset = (int)($_GET['offset'] ?? 0);
    $category = $_GET['category'] ?? null;

    if ($category) {
        $stmt = $pdo->prepare("
            SELECT title, source, url, content, summary, image_url, timestamp, category, rally_originals
            FROM articles WHERE category = ?
            ORDER BY timestamp DESC LIMIT $limit OFFSET $offset
        ");
        $stmt->execute([$category]);
    } else {
        $stmt = $pdo->query("
            SELECT title, source, url, content, summary, image_url, timestamp, category, rally_originals
            FROM articles
            ORDER BY timestamp DESC LIMIT $limit OFFSET $offset
        ");
    }

    echo json_encode($stmt->fetchAll(PDO::FETCH_ASSOC));

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
            (title, source, url, content, summary, image_url, timestamp, category, rally_originals)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
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
        ]);
        if ($stmt->rowCount() > 0) $inserted++;
    }

    echo json_encode(['success' => true, 'inserted' => $inserted]);

} else {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed']);
}
