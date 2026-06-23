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
    CREATE TABLE IF NOT EXISTS rallying_cries (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        date       DATE NOT NULL,
        timestamp  DATETIME NOT NULL,
        content    TEXT NOT NULL,
        stories    JSON NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
");

try {
    $pdo->exec("ALTER TABLE rallying_cries ADD COLUMN stories JSON NULL");
} catch (PDOException $e) {
    // Column already exists
}

if ($method === 'GET') {
    $limit  = min((int)($_GET['limit'] ?? 30), 100);
    $offset = (int)($_GET['offset'] ?? 0);
    $stmt   = $pdo->query("SELECT date, timestamp, content, stories FROM rallying_cries ORDER BY timestamp DESC LIMIT $limit OFFSET $offset");
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    foreach ($rows as &$row) {
        $row['stories'] = $row['stories'] !== null ? json_decode($row['stories'], true) : [];
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

    $entries = isset($data[0]) ? $data : [$data];
    $inserted = 0;
    $stmt = $pdo->prepare("INSERT INTO rallying_cries (date, timestamp, content, stories) VALUES (?, ?, ?, ?)");

    foreach ($entries as $entry) {
        $stories = isset($entry['stories']) ? json_encode($entry['stories']) : null;
        $stmt->execute([
            $entry['date']      ?? date('Y-m-d'),
            $entry['timestamp'] ?? date('Y-m-d H:i:s'),
            $entry['content']   ?? '',
            $stories,
        ]);
        if ($stmt->rowCount() > 0) $inserted++;
    }

    echo json_encode(['success' => true, 'inserted' => $inserted]);

} else {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed']);
}
