// LinkNote 데스크톱 — 앱 시작 시 백엔드(FastAPI)를 자동 실행한다.
use std::env;
use std::fs;
use std::fs::OpenOptions;
use std::io::Write;
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;

fn desktop_log(message: &str) {
    let path = env::temp_dir().join("linknote-desktop.log");
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{message}");
    }
}

fn backend_running() -> bool {
    let addr: SocketAddr = "127.0.0.1:8000".parse().unwrap();
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

fn find_project_root() -> Option<PathBuf> {
    let fallback_roots = [
        "/Users/jeong-yujin/Desktop/LINKNOTE/study-rag-api",
        "/Users/jeong-yujin/Desktop/study-rag-api",
    ];

    for root in fallback_roots {
        let path = PathBuf::from(root);
        if path.join("api_server.py").exists() {
            return Some(path);
        }
    }

    if let Some(home) = env::var_os("HOME") {
        let mut level = vec![PathBuf::from(home).join("Desktop")];
        for _ in 0..=2 {
            let mut next_level = Vec::new();
            for directory in level {
                let candidate = directory.join("study-rag-api");
                if candidate.join("api_server.py").exists() {
                    return Some(candidate);
                }
                if let Ok(entries) = fs::read_dir(directory) {
                    next_level.extend(
                        entries
                            .filter_map(Result::ok)
                            .map(|entry| entry.path())
                            .filter(|path| path.is_dir()),
                    );
                }
            }
            level = next_level;
        }
    }

    if let Ok(manifest_dir) = env::var("CARGO_MANIFEST_DIR") {
        let mut path = PathBuf::from(manifest_dir);
        if path.ends_with("src-tauri") {
            path.pop();
        }
        if path.ends_with("desktop") {
            path.pop();
        }
        if path.join("api_server.py").exists() {
            return Some(path);
        }
    }

    let mut current = env::current_dir().ok()?;
    loop {
        if current.join("api_server.py").exists() {
            return Some(current);
        }
        if !current.pop() {
            break;
        }
    }
    None
}

fn find_python_interpreter(project_root: &PathBuf) -> String {
    let candidates = [
        project_root.join(".venv/bin/python"),
        project_root.join("venv/bin/python"),
        project_root.join(".venv/bin/python3"),
        project_root.join("venv/bin/python3"),
    ];

    for candidate in candidates {
        if candidate.exists() {
            return candidate.to_string_lossy().into_owned();
        }
    }

    "python3".to_string()
}

fn start_backend() {
    if backend_running() {
        desktop_log("backend already running on 127.0.0.1:8000");
        return;
    }

    let Some(project_root) = find_project_root() else {
        desktop_log("project root not found");
        return;
    };

    let python = find_python_interpreter(&project_root);
    desktop_log(&format!(
        "starting backend: root={} python={python}",
        project_root.display()
    ));
    let backend_log_path = env::temp_dir().join("linknote-backend.log");
    let backend_log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(backend_log_path);
    let mut command = Command::new(&python);
    command
        .args([
            "-m",
            "uvicorn",
            "api_server:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ])
        .current_dir(&project_root)
        .env("DATA_DIR", project_root.join("data"))
        .env("CHROMA_PATH", project_root.join("chroma_db"));
    if let Ok(log) = backend_log {
        if let Ok(error_log) = log.try_clone() {
            command
                .stdout(Stdio::from(log))
                .stderr(Stdio::from(error_log));
        }
    }
    match command.spawn() {
        Ok(child) => desktop_log(&format!("backend process spawned: pid={}", child.id())),
        Err(error) => {
            desktop_log(&format!("backend process spawn failed: {error}"));
            return;
        }
    }

    // 백엔드 포트가 열릴 때까지 최대 ~30초 대기
    for _ in 0..60 {
        if backend_running() {
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    desktop_log(if backend_running() {
        "backend became ready"
    } else {
        "backend did not become ready within 30 seconds"
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|_app| {
            start_backend();
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
