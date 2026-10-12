// Tauri 桌面端入口。
//
// 职责：
//   1. 启动时拉起内嵌的 Python 后端（FastAPI），作为子进程。
//   2. 等待后端健康检查通过，再把窗口指过去。
//   3. 窗口关闭时优雅结束后端进程。
//
// 打包说明：
//   - 开发态：使用系统/venv 里的 python。
//   - 打包态：使用随包分发的 python-runtime（见 scripts/build_desktop.ps1）。
//     若未打包运行时，则回退到系统 PATH 中的 python。

use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::Manager;

const BACKEND_PORT: u16 = 8756;

struct BackendProcess(Mutex<Option<Child>>);

/// 定位后端启动脚本与 Python 解释器。
fn resolve_backend(app: &tauri::AppHandle) -> Option<(std::path::PathBuf, std::path::PathBuf)> {
    // 打包态：资源目录下
    let resource_dir = app.path().resource_dir().ok();

    if let Some(rd) = &resource_dir {
        let script = rd.join("backend").join("run_server.py");
        if script.exists() {
            // 优先用随包的 Python 运行时
            for candidate in [
                rd.join("python-runtime").join("python.exe"),
                rd.join("backend").join("python-runtime").join("python.exe"),
            ] {
                if candidate.exists() {
                    return Some((candidate, script));
                }
            }
            // 回退系统 python
            return Some((std::path::PathBuf::from("python"), script));
        }
    }

    // 开发态：仓库根目录
    let dev_root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(|p| p.to_path_buf())?;
    let script = dev_root.join("backend").join("run_server.py");
    if script.exists() {
        // 优先用 venv
        let venv_py = dev_root.join(".venv").join("Scripts").join("python.exe");
        if venv_py.exists() {
            return Some((venv_py, script));
        }
        return Some((std::path::PathBuf::from("python"), script));
    }

    None
}

/// 轮询后端健康检查。
fn wait_for_backend(timeout_secs: u64) -> bool {
    let url = format!("http://127.0.0.1:{}/api/health", BACKEND_PORT);
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };

    let deadline = std::time::Instant::now() + Duration::from_secs(timeout_secs);
    while std::time::Instant::now() < deadline {
        if let Ok(resp) = client.get(&url).send() {
            if resp.status().is_success() {
                return true;
            }
        }
        std::thread::sleep(Duration::from_millis(400));
    }
    false
}

fn spawn_backend(app: &tauri::AppHandle) -> Option<Child> {
    let (python, script) = resolve_backend(app)?;

    let mut cmd = Command::new(&python);
    cmd.arg(&script)
        .arg("--port")
        .arg(BACKEND_PORT.to_string())
        .arg("--host")
        .arg("127.0.0.1");

    // 打包态隐藏控制台窗口
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd.stdout(Stdio::null()).stderr(Stdio::null());

    match cmd.spawn() {
        Ok(child) => Some(child),
        Err(e) => {
            eprintln!("启动后端失败: {} (python={:?})", e, python);
            None
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcess(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();

            // 后台线程启动后端，避免阻塞窗口显示
            std::thread::spawn(move || {
                let child = spawn_backend(&handle);
                let state = handle.state::<BackendProcess>();
                *state.0.lock().unwrap() = child;

                if wait_for_backend(90) {
                    println!("后端已就绪: http://127.0.0.1:{}", BACKEND_PORT);
                    if let Some(win) = handle.get_webview_window("main") {
                        let url = format!("http://127.0.0.1:{}", BACKEND_PORT);
                        let _ = win.eval(&format!("window.location.replace('{}')", url));
                    }
                } else {
                    eprintln!("后端启动超时");
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let state = window.state::<BackendProcess>();
                if let Some(mut child) = state.0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Tauri 应用启动失败");
}
