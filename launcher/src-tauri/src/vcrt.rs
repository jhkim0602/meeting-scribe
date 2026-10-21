//! 윈도우: C++ 런타임(msvcp140.dll 등)을 앱의 파이썬 폴더에 넣어 둔다 (app-local).
//!
//! PyTorch 같은 패키지의 DLL은 MSVC C++ 런타임을 쓴다. 앱 전용 파이썬에는 vcruntime140.dll만 들어 있어서
//! msvcp140.dll은 System32에서 찾는데, 런타임이 오래된 PC(예: 14.13)에서는 두 버전이 섞여
//! `import torch`가 WinError 1114(c10.dll)로 죽고 서버가 켜지지 않는다.
//! 윈도우는 DLL을 실행 파일(python.exe) 폴더에서 System32보다 먼저 찾으므로, 앱에 넣어 둔 한 벌을
//! 그 폴더에 두면 시스템에 무엇이 깔려 있든 같은 버전을 쓴다. 관리자 권한도, 시스템 변경도 없다.
//! (맥·리눅스에는 앱에 든 런타임이 없어서 아무것도 하지 않는다.)

use crate::paths::Paths;
use std::fs;
use std::path::Path;

/// 앱에 든 런타임을 파이썬 폴더에 맞춰 둔다. 없거나 더 오래된 것만 바꾸고, 바꾼 파일 이름을 돌려준다.
/// 파이썬을 새로 받거나(uv가 새 패치 버전으로 바꿀 때) 앱을 업데이트해도 맞도록 서버를 켤 때마다 부른다.
pub fn ensure(paths: &Paths) -> Result<Vec<String>, String> {
    let src = paths.vcrt();
    match paths.python_home() {
        Some(home) if src.is_dir() && home.is_dir() => sync(&src, &home),
        _ => Ok(vec![]),
    }
}

fn sync(src: &Path, dest: &Path) -> Result<Vec<String>, String> {
    let mut changed = Vec::new();
    let mut errors = Vec::new();
    for entry in fs::read_dir(src).map_err(|e| e.to_string())? {
        let from = entry.map_err(|e| e.to_string())?.path();
        if !from.extension().is_some_and(|e| e.eq_ignore_ascii_case("dll")) {
            continue;
        }
        let name = from.file_name().unwrap_or_default().to_string_lossy().to_string();
        let to = dest.join(&name);
        // 파이썬에 든 것(vcruntime140.dll)이 더 새것이면 그대로 둔다 (새 vcruntime + 옛 msvcp는 괜찮다)
        let copy = !to.exists() || matches!((file_version(&from), file_version(&to)), (Some(a), Some(b)) if a > b);
        if !copy {
            continue;
        }
        // 다른 파이썬(예: Claude 연결)이 켜져 있으면 쓰는 중인 DLL은 덮어쓰지 못한다. 나머지는 계속한다.
        match fs::copy(&from, &to) {
            Ok(_) => changed.push(name),
            Err(e) => errors.push(format!("{name}: {e}")),
        }
    }
    if errors.is_empty() {
        Ok(changed)
    } else {
        Err(errors.join(", "))
    }
}

/// DLL의 파일 버전 (리소스의 VS_FIXEDFILEINFO). 14.44.35211.0 → 0x000E_002C_898B_0000
fn file_version(path: &Path) -> Option<u64> {
    let bytes = fs::read(path).ok()?;
    let at = bytes.windows(4).position(|w| w == [0xBD, 0x04, 0xEF, 0xFE])?;
    let word = |i: usize| bytes.get(at + i..at + i + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]) as u64);
    Some(word(8)? << 32 | word(12)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fake_dll(v: [u16; 4]) -> Vec<u8> {
        let mut b = b"MZ junk".to_vec();
        b.extend_from_slice(&0xFEEF_04BDu32.to_le_bytes());
        b.extend_from_slice(&0x0001_0000u32.to_le_bytes());
        b.extend_from_slice(&((v[0] as u32) << 16 | v[1] as u32).to_le_bytes());
        b.extend_from_slice(&((v[2] as u32) << 16 | v[3] as u32).to_le_bytes());
        b
    }

    #[test]
    fn reads_file_version() {
        let dir = std::env::temp_dir().join(format!("ms-vcrt-v-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let (a, b) = (dir.join("a.dll"), dir.join("b.dll"));
        fs::write(&a, fake_dll([14, 44, 35211, 0])).unwrap();
        fs::write(&b, fake_dll([14, 13, 26118, 0])).unwrap();
        assert_eq!(file_version(&a), Some(14 << 48 | 44 << 32 | 35211 << 16));
        assert!(file_version(&a) > file_version(&b));
        fs::write(&b, "no version").unwrap();
        assert_eq!(file_version(&b), None);
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn copies_missing_and_older_keeps_newer() {
        let dir = std::env::temp_dir().join(format!("ms-vcrt-s-{}", std::process::id()));
        let (src, dest) = (dir.join("vcrt"), dir.join("python"));
        fs::create_dir_all(&src).unwrap();
        fs::create_dir_all(&dest).unwrap();
        fs::write(src.join("msvcp140.dll"), fake_dll([14, 44, 1, 0])).unwrap();
        fs::write(src.join("vcruntime140.dll"), fake_dll([14, 44, 1, 0])).unwrap();
        fs::write(src.join("concrt140.dll"), fake_dll([14, 44, 1, 0])).unwrap();
        fs::write(src.join("readme.txt"), "x").unwrap();
        fs::write(dest.join("vcruntime140.dll"), fake_dll([14, 50, 0, 0])).unwrap(); // 파이썬 것이 더 새것
        fs::write(dest.join("concrt140.dll"), fake_dll([14, 13, 0, 0])).unwrap(); // 더 오래된 것

        let mut changed = sync(&src, &dest).unwrap();
        changed.sort();
        assert_eq!(changed, ["concrt140.dll", "msvcp140.dll"]);
        assert_eq!(fs::read(dest.join("vcruntime140.dll")).unwrap(), fake_dll([14, 50, 0, 0]));
        assert_eq!(fs::read(dest.join("concrt140.dll")).unwrap(), fake_dll([14, 44, 1, 0]));
        assert!(!dest.join("readme.txt").exists());
        assert!(sync(&src, &dest).unwrap().is_empty()); // 두 번째부터는 할 일이 없다
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn nothing_to_do_without_bundled_runtime() {
        let dir = std::env::temp_dir().join(format!("ms-vcrt-n-{}", std::process::id()));
        let paths = Paths { support: dir.join("s"), resources: dir.join("r") };
        assert!(ensure(&paths).unwrap().is_empty());
    }
}
