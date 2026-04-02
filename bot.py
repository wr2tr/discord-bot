#![windows_subsystem = "windows"]
#[cfg(windows)]
use std::os::windows::process::CommandExt;

use eframe::egui;
use eframe::egui::{
    Color32, ColorImage, FontFamily, FontId, Pos2, Rect,
    RichText, Rounding, Sense, Stroke, TextureHandle, Vec2,
};
use rdev::{listen, Event, EventType, Key};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[cfg(windows)]
#[link(name = "ntdll")]
extern "system" {
    fn NtSetTimerResolution(desired: u32, set: u8, current: *mut u32) -> i32;
    fn NtDelayExecution(alertable: u8, interval: *const i64) -> i32;
}
#[cfg(windows)]
extern crate winapi;

#[cfg(windows)]
#[link(name = "avrt")]
extern "system" {
    fn AvSetMmThreadCharacteristicsA(task_name: *const i8, task_index: *mut u32) -> winapi::um::winnt::HANDLE;
    #[allow(dead_code)]
    #[allow(dead_code)]
    fn AvRevertMmThreadCharacteristics(avrt_handle: winapi::um::winnt::HANDLE) -> i32;
}

// ══════════════════════════════════════════════════════════════════════════════
// HOTKEY TYPE
// ══════════════════════════════════════════════════════════════════════════════
#[derive(Clone, PartialEq, Debug)]
enum HotKey {
    Keyboard(Key),
    MouseBtn(u32),  // raw button code — detected at bind time
}
impl HotKey {
    fn display(&self) -> String {
        match self {
            HotKey::Keyboard(k) => key_name(k).to_string(),
            HotKey::MouseBtn(n) => format!("Mouse {}", n),
        }
    }
}
fn btn_to_num(b: &rdev::Button) -> u32 {
    match b {
        rdev::Button::Left    => 1,
        rdev::Button::Right   => 2,
        rdev::Button::Middle  => 3,
        rdev::Button::Unknown(n) => *n as u32,
    }
}

fn key_name(k: &Key) -> &str {
    match k {
        Key::KeyA=>"A",Key::KeyB=>"B",Key::KeyC=>"C",Key::KeyD=>"D",Key::KeyE=>"E",
        Key::KeyF=>"F",Key::KeyG=>"G",Key::KeyH=>"H",Key::KeyI=>"I",Key::KeyJ=>"J",
        Key::KeyK=>"K",Key::KeyL=>"L",Key::KeyM=>"M",Key::KeyN=>"N",Key::KeyO=>"O",
        Key::KeyP=>"P",Key::KeyQ=>"Q",Key::KeyR=>"R",Key::KeyS=>"S",Key::KeyT=>"T",
        Key::KeyU=>"U",Key::KeyV=>"V",Key::KeyW=>"W",Key::KeyX=>"X",Key::KeyY=>"Y",
        Key::KeyZ=>"Z",Key::Num0=>"0",Key::Num1=>"1",Key::Num2=>"2",Key::Num3=>"3",
        Key::Num4=>"4",Key::Num5=>"5",Key::Num6=>"6",Key::Num7=>"7",Key::Num8=>"8",
        Key::Num9=>"9",Key::F1=>"F1",Key::F2=>"F2",Key::F3=>"F3",Key::F4=>"F4",
        Key::F5=>"F5",Key::F6=>"F6",Key::F7=>"F7",Key::F8=>"F8",Key::F9=>"F9",
        Key::F10=>"F10",Key::F11=>"F11",Key::F12=>"F12",Key::Tab=>"TAB",_=>"?",
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// KEY SYSTEM
// ══════════════════════════════════════════════════════════════════════════════
const BAN_CHECK_URL: &str = "https://discord-bot-production-cc70.up.railway.app/bans";

const SECRET: u64       = 0xA3F7_C291_5E6B_D840;
const DEV_PASSWORD: &str = "NATIVE2025DEV";
const DEV_OWNER_HW: u64 = 0x28376045325BD3EA;

fn fnv64(data: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for &b in data { h ^= b as u64; h = h.wrapping_mul(0x00000001000000b3); }
    h
}
fn get_hw_id() -> u64 {
    let c = std::env::var("COMPUTERNAME").unwrap_or_else(|_| "X".into());
    let u = std::env::var("USERNAME").unwrap_or_else(|_| "X".into());
    fnv64(format!("NATIVE-{}-{}", c.to_uppercase(), u.to_uppercase()).as_bytes())
}
fn derive_key(hw: u64) -> String {
    let a = fnv64(&(hw ^ SECRET).to_le_bytes());
    let b = fnv64(&(hw ^ SECRET ^ 0x1234567890abcdef).to_le_bytes());
    let c = fnv64(&(hw ^ SECRET ^ 0xfedcba9876543210).to_le_bytes());
    format!("NTVE-{:04X}-{:04X}-{:04X}-{:04X}-{:04X}",
        (a>>48) as u16,(a>>32) as u16,(b>>48) as u16,(b>>32) as u16,(c>>48) as u16)
}
fn validate_key(key: &str) -> bool {
    let trimmed = key.trim().to_uppercase();
    // Key is always 29 chars: NTVE-XXXX-XXXX-XXXX-XXXX-XXXX
    let clean = if trimmed.len() >= 29 && trimmed.starts_with("NTVE-") {
        &trimmed[..29]
    } else {
        trimmed.split(':').next().unwrap_or(&trimmed)
    };
    // Check key matches machine
    if clean != derive_key(get_hw_id()) { return false; }
    // Check expiry — anything after position 29 (with or without colon) is the timestamp
    let suffix = if trimmed.len() > 29 {
        trimmed[29..].trim_start_matches(':').trim()
    } else { "" };
    if !suffix.is_empty() && suffix != "NEVER" {
        if let Ok(exp) = suffix.parse::<u64>() {
            let now_ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default().as_secs();
            if now_ts >= exp { return false; } // expired — reject
        }
    }
    true
}
fn is_dev_owner() -> bool { get_hw_id() == DEV_OWNER_HW }

fn license_path() -> Option<std::path::PathBuf> {
    std::env::var("APPDATA").ok().map(|p| {
        let mut path = std::path::PathBuf::from(p);
        path.push("Native"); path.push("license.key"); path
    })
}
fn save_license(key: &str) {
    if let Some(path) = license_path() {
        if let Some(parent) = path.parent() { std::fs::create_dir_all(parent).ok(); }
        let trimmed = key.trim().to_uppercase();
        // Key is always exactly 29 chars: NTVE-XXXX-XXXX-XXXX-XXXX-XXXX
        // Anything after position 29 is the expiry timestamp (with or without colon)
        let key_part = if trimmed.len() >= 29 { &trimmed[..29] } else { &trimmed };
        let suffix   = if trimmed.len() > 29 { trimmed[29..].trim_start_matches(':') } else { "" };
        let expiry   = if suffix.is_empty() { "NEVER".to_string() } else { suffix.to_string() };
        let hw       = format!("{:016X}", get_hw_id());
        let content  = format!("{}:{}:{}", key_part, hw, expiry);
        std::fs::write(path, content).ok();
    }
}
fn load_expiry() -> Option<u64> {
    let path = {
        let mut p = std::env::var("APPDATA").map(std::path::PathBuf::from).unwrap_or_else(|_| std::path::PathBuf::from("."));
        p.push("Native"); p.push("license.key");
        p
    };
    let text = std::fs::read_to_string(&path).ok()?;
    // Format on disk: NTVE-XXXX-XXXX-XXXX-XXXX-XXXX:HWID:EXPIRY
    // Split by ':' — key part has dashes not colons, so parts are:
    // [0] = NTVE-XXXX-XXXX-XXXX-XXXX-XXXX, [1] = HWID, [2] = EXPIRY
    let parts: Vec<&str> = text.trim().split(':').collect();
    let expiry_str = parts.get(2)?;
    if *expiry_str == "NEVER" { return None; }
    expiry_str.parse::<u64>().ok()
}

// ══════════════════════════════════════════════════════════════════════════════
// SETTINGS
// ══════════════════════════════════════════════════════════════════════════════
fn settings_path() -> Option<std::path::PathBuf> {
    std::env::var("APPDATA").ok().map(|p| {
        let mut path = std::path::PathBuf::from(p);
        path.push("Native"); path.push("settings.cfg"); path
    })
}
struct Settings {
    kps: u64, jitter: u64, button: u8, toggle_mode: bool,
    burst_count: u64, always_on_top: bool, hotkey: String,
    accent_r: u8, accent_g: u8, accent_b: u8,
    outline_r: u8, outline_g: u8, outline_b: u8,
    panel_a: u8,
    key_enabled: bool, key_vk: u16,
}
impl Default for Settings {
    fn default() -> Self { Self {
        kps:20, jitter:0, button:0, toggle_mode:false,
        burst_count:1, always_on_top:false, hotkey:"KeyF".into(),
        accent_r:0, accent_g:180, accent_b:255,
        outline_r:0, outline_g:90, outline_b:175, panel_a:235,
        key_enabled:false, key_vk:0x46,
    }}
}
impl Settings {
    fn save(&self) {
        if let Some(path) = settings_path() {
            if let Some(p) = path.parent() { std::fs::create_dir_all(p).ok(); }
            std::fs::write(path, format!(
                "kps={}\njitter={}\nbutton={}\ntoggle={}\nburst={}\naot={}\nhotkey={}\naccent={},{},{}\noutline={},{},{}\npanel_a={}\nkeyenabled={}\nkeyvk={}",
                self.kps,self.jitter,self.button,self.toggle_mode as u8,
                self.burst_count,self.always_on_top as u8,self.hotkey,
                self.accent_r,self.accent_g,self.accent_b,
                self.outline_r,self.outline_g,self.outline_b,self.panel_a,
            self.key_enabled as u8, self.key_vk,
            )).ok();
        }
    }
    fn load() -> Self {
        let mut s = Settings::default();
        let path = match settings_path() { Some(p)=>p, None=>return s };
        let content = match std::fs::read_to_string(path) { Ok(c)=>c, Err(_)=>return s };
        for line in content.lines() {
            let mut parts = line.splitn(2,'=');
            let k = parts.next().unwrap_or("").trim();
            let v = parts.next().unwrap_or("").trim();
            match k {
                "kps"     => { if let Ok(x)=v.parse(){s.kps=x;} }
                "jitter"  => { if let Ok(x)=v.parse(){s.jitter=x;} }
                "button"  => { if let Ok(x)=v.parse(){s.button=x;} }
                "toggle"  => { s.toggle_mode=v=="1"; }
                "burst"   => { if let Ok(x)=v.parse(){s.burst_count=x;} }
                "aot"     => { s.always_on_top=v=="1"; }
                "hotkey"  => { s.hotkey=v.to_string(); }
                "accent"  => { let c:Vec<u8>=v.split(',').filter_map(|x|x.parse().ok()).collect();
                                if c.len()==3{s.accent_r=c[0];s.accent_g=c[1];s.accent_b=c[2];} }
                "outline" => { let c:Vec<u8>=v.split(',').filter_map(|x|x.parse().ok()).collect();
                                if c.len()==3{s.outline_r=c[0];s.outline_g=c[1];s.outline_b=c[2];} }
                "panel_a" => { if let Ok(x)=v.parse(){s.panel_a=x;} }
                "keyenabled" => { s.key_enabled = v.trim()=="1"; }
                "keyvk"      => { if let Ok(x)=v.parse(){s.key_vk=x;} }
                _ => {}
            }
        }
        s
    }
}

// ── User Presets ──────────────────────────────────────────────────────────────
#[derive(Clone)]
struct Preset {
    name:     String,
    kps:      u64,
    accent:   [u8;3],
    outline:  [u8;3],
    panel_a:  u8,
    bg_path:  String, // empty = default
}
impl Preset {
    fn presets_dir() -> Option<std::path::PathBuf> {
        std::env::var("APPDATA").ok().map(|a| {
            let mut p = std::path::PathBuf::from(a);
            p.push("Native"); p.push("presets"); p
        })
    }
    fn save_all(presets: &[Preset]) {
        if let Some(dir) = Self::presets_dir() {
            std::fs::create_dir_all(&dir).ok();
            // Write index file
            let lines: Vec<String> = presets.iter().enumerate().map(|(i, p)| {
                format!("{}|{}|{},{},{}|{},{},{}|{}|{}",
                    i, p.name,
                    p.accent[0],p.accent[1],p.accent[2],
                    p.outline[0],p.outline[1],p.outline[2],
                    p.panel_a, p.kps)
            }).collect();
            std::fs::write(dir.join("index.cfg"), lines.join("\n")).ok();
            // Write bg paths
            let bg_lines: Vec<String> = presets.iter().enumerate().map(|(i,p)| {
                format!("{}={}", i, p.bg_path)
            }).collect();
            std::fs::write(dir.join("bg.cfg"), bg_lines.join("\n")).ok();
        }
    }
    fn load_all() -> Vec<Preset> {
        let dir = match Self::presets_dir() { Some(d)=>d, None=>return vec![] };
        let index = match std::fs::read_to_string(dir.join("index.cfg")) { Ok(s)=>s, Err(_)=>return vec![] };
        let bg_map: std::collections::HashMap<usize,String> = std::fs::read_to_string(dir.join("bg.cfg"))
            .unwrap_or_default().lines().filter_map(|l| {
                let mut p = l.splitn(2,'=');
                let i = p.next()?.trim().parse::<usize>().ok()?;
                let v = p.next()?.to_string();
                Some((i,v))
            }).collect();
        index.lines().enumerate().filter_map(|(i,line)| {
            let parts: Vec<&str> = line.splitn(10,'|').collect();
            if parts.len() < 6 { return None; }
            let name    = parts[1].to_string();
            let ac: Vec<u8> = parts[2].split(',').filter_map(|x|x.parse().ok()).collect();
            let ol: Vec<u8> = parts[3].split(',').filter_map(|x|x.parse().ok()).collect();
            let panel_a = parts[4].parse::<u8>().unwrap_or(220);
            let kps     = parts[5].parse::<u64>().unwrap_or(20);
            if ac.len()<3||ol.len()<3 {return None;}
            Some(Preset {
                name, kps,
                accent:  [ac[0],ac[1],ac[2]],
                outline: [ol[0],ol[1],ol[2]],
                panel_a,
                bg_path: bg_map.get(&i).cloned().unwrap_or_default(),
            })
        }).collect()
    }
}

fn is_licensed() -> bool {
    let hw = get_hw_id();
    if let Some(path) = license_path() {
        if let Ok(c) = std::fs::read_to_string(path) {
            let parts: Vec<&str> = c.trim().splitn(3, ':').collect();
            let key    = parts.get(0).unwrap_or(&"").trim().to_uppercase();
            let stored: u64 = parts.get(1)
                .and_then(|s| u64::from_str_radix(s.trim(), 16).ok())
                .unwrap_or(0);
            if stored != hw { return false; }
            if key != derive_key(hw) { return false; }
            if let Some(expiry_str) = parts.get(2) {
                if *expiry_str != "NEVER" {
                    if let Ok(exp) = expiry_str.trim().parse::<u64>() {
                        let now = std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default().as_secs();
                        if now >= exp { return false; }
                    }
                }
            }
            return true;
        }
    }
    false
}

fn dev_unlock_path() -> Option<std::path::PathBuf> {
    std::env::var("APPDATA").ok().map(|a| {
        let mut p = std::path::PathBuf::from(a);
        p.push("Native"); p.push("dev.unlock"); p
    })
}

fn is_dev_unlocked() -> bool {
    dev_unlock_path().map(|p| p.exists()).unwrap_or(false)
}

fn save_dev_unlock() {
    if let Some(p) = dev_unlock_path() {
        if let Some(parent) = p.parent() { std::fs::create_dir_all(parent).ok(); }
        std::fs::write(p, "unlocked").ok();
    }
}

fn revoke_dev_unlock() {
    if let Some(p) = dev_unlock_path() { std::fs::remove_file(p).ok(); }
}


fn ban_cache_path() -> Option<std::path::PathBuf> {
    std::env::var("APPDATA").ok().map(|a| {
        let mut p = std::path::PathBuf::from(a);
        p.push("Native"); p.push("banned.cfg"); p
    })
}

fn is_banned() -> bool {
    let hw = format!("{:016X}", get_hw_id());
    if let Some(p) = ban_cache_path() {
        if let Ok(content) = std::fs::read_to_string(p) {
            return content.lines().any(|l| l.trim().eq_ignore_ascii_case(&hw));
        }
    }
    false
}

// Downloads ban list from bot every 10s silently in background
fn spawn_ban_checker() {
    std::thread::spawn(|| {
        loop {
            // Use PowerShell hidden window to download ban list
            let result = std::process::Command::new("powershell")
                .args([
                    "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                    "-Command",
                    &format!(
                        "try{{(Invoke-WebRequest -Uri '{}' -TimeoutSec 5 -UseBasicParsing).Content}}catch{{''}}",
                        BAN_CHECK_URL
                    ),
                ])
                .creation_flags(0x08000000) // CREATE_NO_WINDOW
                .output();

            if let Ok(out) = result {
                let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
                // Only write if we got a valid response (not empty)
                if !text.is_empty() || text == "" {
                    if let Some(p) = ban_cache_path() {
                        if let Some(parent) = p.parent() {
                            std::fs::create_dir_all(parent).ok();
                        }
                        std::fs::write(p, &text).ok();
                    }
                }
            }

            std::thread::sleep(std::time::Duration::from_secs(10));
        }
    });
}

fn hotkey_to_string_full(h: &HotKey) -> String {
    match h {
        HotKey::Keyboard(k) => format!("{:?}", k),
        HotKey::MouseBtn(n) => format!("MouseBtn:{}", n),
    }
}
fn string_to_hotkey_full(s: &str) -> HotKey {
    if let Some(rest) = s.strip_prefix("MouseBtn:") {
        if let Ok(n) = rest.parse::<u32>() {
            return HotKey::MouseBtn(n);
        }
    }
    HotKey::Keyboard(string_to_hotkey(s))
}
fn string_to_hotkey(s: &str) -> Key {
    match s {
        "KeyA"=>Key::KeyA,"KeyB"=>Key::KeyB,"KeyC"=>Key::KeyC,"KeyD"=>Key::KeyD,
        "KeyE"=>Key::KeyE,"KeyF"=>Key::KeyF,"KeyG"=>Key::KeyG,"KeyH"=>Key::KeyH,
        "KeyI"=>Key::KeyI,"KeyJ"=>Key::KeyJ,"KeyK"=>Key::KeyK,"KeyL"=>Key::KeyL,
        "KeyM"=>Key::KeyM,"KeyN"=>Key::KeyN,"KeyO"=>Key::KeyO,"KeyP"=>Key::KeyP,
        "KeyQ"=>Key::KeyQ,"KeyR"=>Key::KeyR,"KeyS"=>Key::KeyS,"KeyT"=>Key::KeyT,
        "KeyU"=>Key::KeyU,"KeyV"=>Key::KeyV,"KeyW"=>Key::KeyW,"KeyX"=>Key::KeyX,
        "KeyY"=>Key::KeyY,"KeyZ"=>Key::KeyZ,
        "Num0"=>Key::Num0,"Num1"=>Key::Num1,"Num2"=>Key::Num2,"Num3"=>Key::Num3,
        "Num4"=>Key::Num4,"Num5"=>Key::Num5,"Num6"=>Key::Num6,"Num7"=>Key::Num7,
        "Num8"=>Key::Num8,"Num9"=>Key::Num9,
        "F1"=>Key::F1,"F2"=>Key::F2,"F3"=>Key::F3,"F4"=>Key::F4,"F5"=>Key::F5,
        "F6"=>Key::F6,"F7"=>Key::F7,"F8"=>Key::F8,"F9"=>Key::F9,"F10"=>Key::F10,
        "F11"=>Key::F11,"F12"=>Key::F12,"Tab"=>Key::Tab,
        _ => Key::KeyF,
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// CLICK ENGINE
// ══════════════════════════════════════════════════════════════════════════════
#[cfg(windows)]
mod fast_click {
    use std::mem;
    use winapi::um::winuser::{
        SendInput,INPUT,INPUT_MOUSE,INPUT_KEYBOARD,
        MOUSEEVENTF_LEFTDOWN,MOUSEEVENTF_LEFTUP,
        MOUSEEVENTF_RIGHTDOWN,MOUSEEVENTF_RIGHTUP,MOUSEINPUT,
        KEYBDINPUT,KEYEVENTF_KEYUP,
    };
    #[inline(always)]
    fn mi(flags:u32)->INPUT{INPUT{type_:INPUT_MOUSE,u:unsafe{
        let mut u:winapi::um::winuser::INPUT_u=mem::zeroed();
        *u.mi_mut()=MOUSEINPUT{dx:0,dy:0,mouseData:0,dwFlags:flags,time:0,dwExtraInfo:0};
        u
    }}}
    #[inline(always)]
    fn ki(vk:u16,flags:u32)->INPUT{INPUT{type_:INPUT_KEYBOARD as u32,u:unsafe{
        let mut u:winapi::um::winuser::INPUT_u=mem::zeroed();
        *u.ki_mut()=KEYBDINPUT{wVk:vk,wScan:0,dwFlags:flags,time:0,dwExtraInfo:0};
        u
    }}}
    pub struct Clicker {
        left:[INPUT;2], right:[INPUT;2], both:[INPUT;4],
        left_key:[INPUT;4], right_key:[INPUT;4], both_key:[INPUT;6],
        cur_vk:u16,
    }
    impl Clicker {
        pub fn new()->Self{
            let(ld,lu,rd,ru)=(mi(MOUSEEVENTF_LEFTDOWN),mi(MOUSEEVENTF_LEFTUP),
                              mi(MOUSEEVENTF_RIGHTDOWN),mi(MOUSEEVENTF_RIGHTUP));
            let(kd,ku)=(ki(0,0),ki(0,KEYEVENTF_KEYUP));
            Self{left:[ld,lu],right:[rd,ru],both:[ld,lu,rd,ru],
                 left_key:[ld,lu,kd,ku],right_key:[rd,ru,kd,ku],
                 both_key:[ld,lu,rd,ru,kd,ku],cur_vk:0}
        }
        #[inline(always)]
        pub fn set_key(&mut self,vk:u16){
            if vk!=self.cur_vk{
                self.cur_vk=vk;
                self.left_key[2]=ki(vk,0);  self.left_key[3]=ki(vk,KEYEVENTF_KEYUP);
                self.right_key[2]=ki(vk,0); self.right_key[3]=ki(vk,KEYEVENTF_KEYUP);
                self.both_key[4]=ki(vk,0);  self.both_key[5]=ki(vk,KEYEVENTF_KEYUP);
            }
        }
        #[inline(always)]
        pub fn click(&mut self,btn:u8){
            let sz=mem::size_of::<INPUT>() as i32;
            unsafe{match btn{
                1=>{SendInput(2,self.right.as_mut_ptr(),sz);}
                2=>{SendInput(4,self.both.as_mut_ptr(),sz);}
                _=>{SendInput(2,self.left.as_mut_ptr(),sz);}
            }}
        }
        #[inline(always)]
        pub fn click_and_key(&mut self,btn:u8){
            let sz=mem::size_of::<INPUT>() as i32;
            unsafe{match btn{
                1=>{SendInput(4,self.right_key.as_mut_ptr(),sz);}
                2=>{SendInput(6,self.both_key.as_mut_ptr(),sz);}
                _=>{SendInput(4,self.left_key.as_mut_ptr(),sz);}
            }}
        }
    }
}

const S_ENABLED:  u64 = 1<<0;
const S_CLICKING: u64 = 1<<1;
const S_TOGGLE:   u64 = 1<<2;
const S_BTN_SHF:  u64 = 3;
const S_BTN_MASK: u64 = 0b11<<S_BTN_SHF;
const S_JIT_SHF:  u64 = 5;
const S_JIT_MASK: u64 = 0x7F<<S_JIT_SHF;
const S_KPS_SHF:  u64 = 12;
const S_KPS_MASK: u64 = 0x7FFF<<S_KPS_SHF;
const S_VK_SHF:   u64 = 27;
const S_VK_MASK:  u64 = 0xFFFF<<27;
const S_KEY_ON:   u64 = 1<<43;
#[inline(always)] fn pack_kps(k:u64)->u64{k.clamp(1,10_000)<<S_KPS_SHF}
#[inline(always)] fn pack_vk(v:u64)->u64{(v&0xFFFF)<<S_VK_SHF}
#[inline(always)] fn unpack_vk(s:u64)->u16{((s&S_VK_MASK)>>S_VK_SHF) as u16}
#[inline(always)] fn pack_btn(b:u64)->u64{(b&0b11)<<S_BTN_SHF}
#[inline(always)] fn pack_jit(j:u64)->u64{j.min(50)<<S_JIT_SHF}
#[inline(always)] fn unpack_kps(s:u64)->u64{((s&S_KPS_MASK)>>S_KPS_SHF).max(1)}
#[inline(always)] fn unpack_btn(s:u64)->u8{((s&S_BTN_MASK)>>S_BTN_SHF) as u8}
#[inline(always)] fn unpack_jit(s:u64)->u64{(s&S_JIT_MASK)>>S_JIT_SHF}

#[repr(align(64))]
struct Shared {
    state:        AtomicU64,
    total_clicks: AtomicU64,
    key_held:     AtomicBool,
    fast_key_down: AtomicBool, // set by GetAsyncKeyState poller
    _pad:         [u8;54],  // key_vk now packed in state word
}
impl Shared {
    fn new(kps:u64)->Self{
        Self{state:AtomicU64::new(S_ENABLED|pack_kps(kps)),
             total_clicks:AtomicU64::new(0),
             fast_key_down:AtomicBool::new(false),
             key_held:AtomicBool::new(false),_pad:[0;54]}
    }
    #[inline(always)] fn load(&self)->u64{self.state.load(Ordering::Relaxed)}
    fn set_enabled(&self,v:bool){if v{self.state.fetch_or(S_ENABLED,Ordering::Relaxed);}else{self.state.fetch_and(!S_ENABLED,Ordering::Relaxed);}}
    fn set_clicking(&self,v:bool){if v{self.state.fetch_or(S_CLICKING,Ordering::Relaxed);}else{self.state.fetch_and(!S_CLICKING,Ordering::Relaxed);}}
    fn set_key_vk(&self,vk:u16,on:bool){
        // Atomically pack both vk and key_on flag into state word
        self.state.fetch_update(Ordering::Relaxed,Ordering::Relaxed,|s|{
            let s = s & !(S_VK_MASK|S_KEY_ON); // clear old
            let s = s | pack_vk(vk as u64);
            let s = if on {s|S_KEY_ON} else {s};
            Some(s)
        }).ok();
    }
    fn set_toggle(&self,v:bool){if v{self.state.fetch_or(S_TOGGLE,Ordering::Relaxed);}else{self.state.fetch_and(!S_TOGGLE,Ordering::Relaxed);}}
    fn set_kps(&self,k:u64){let s=self.load();self.state.store((s&!S_KPS_MASK)|pack_kps(k),Ordering::Relaxed);}
    fn set_btn(&self,b:u8){let s=self.load();self.state.store((s&!S_BTN_MASK)|pack_btn(b as u64),Ordering::Relaxed);}
    fn set_jit(&self,j:u64){let s=self.load();self.state.store((s&!S_JIT_MASK)|pack_jit(j),Ordering::Relaxed);}
}

#[cfg(target_arch="x86_64")]
#[inline(always)] fn rdtsc()->u64{
    unsafe{
        // LFENCE serializes the instruction stream before RDTSC
        // prevents out-of-order execution from giving stale timestamps
        core::arch::x86_64::_mm_lfence();
        core::arch::x86_64::_rdtsc()
    }
}
#[cfg(not(target_arch="x86_64"))]
#[inline(always)] fn rdtsc()->u64{Instant::now().elapsed().as_nanos() as u64}

fn calibrate_tsc()->u64{
    // Single 100ms sample — accurate, only runs once at startup
    let i0=Instant::now(); let c0=rdtsc();
    thread::sleep(Duration::from_millis(100));
    let c1=rdtsc();
    ((c1-c0)*1024)/i0.elapsed().as_nanos().max(1) as u64
}
#[inline(always)] fn xs64(s:&mut u64)->u64{*s^=*s<<13;*s^=*s>>7;*s^=*s<<17;*s}

// ══════════════════════════════════════════════════════════════════════════════
// APP
// ══════════════════════════════════════════════════════════════════════════════
#[derive(Clone)]
struct Theme {
    accent:Color32, outline:Color32, text:Color32, text_dim:Color32, panel:Color32,
}
impl Default for Theme {
    fn default()->Self{Self{
        accent:  Color32::from_rgb(0,185,255),
        outline: Color32::from_rgb(0,95,185),
        text:    Color32::from_rgb(240,250,255),
        text_dim:Color32::from_rgb(140,180,220),
        panel:   Color32::from_rgba_premultiplied(8,13,30,240),
    }}
}

#[derive(PartialEq)] enum Tab { Main, Theme, Dev }
#[derive(PartialEq)] enum Screen { License, DevPanel, Main }

struct App {
    screen:Screen, tab:Tab, theme:Theme,
    // license
    key_input:String, key_error:String, key_ok_flash:f32,
    // dev
    dev_pw_input:String, dev_hw_input:String, dev_generated:String, dev_unlocked:bool,
    // clicker engine
    shared:Arc<Shared>, hotkey:Arc<Mutex<HotKey>>,
    kps_input:String, binding:bool,
    pulse:f32, anim:f32,
    session_start:Option<Instant>,
    total_display:u64, click_flash:f32,
    always_on_top:bool, burst_count:u64,
    binding_btn: Option<u32>,  // set by listen thread during rebind
    last_sample_time:Instant, measured_cps:f64, last_sample_clicks:u64,
    // expiry
    key_expiry: Option<u64>,  // unix timestamp, None = permanent
    // bg
    bg_texture:Option<TextureHandle>,
    bg_brightness: u8,  // 0=very dark, 255=full bright
    // user presets
    presets: Vec<Preset>,
    preset_name_input: String,
    binding_mouse: Arc<AtomicU64>,
    key_enabled: bool,
    key_vk: u16,
    // auto sof
    // cps graph
    cps_history:[f32;50], cps_hist_idx:usize,
}

impl App {
    fn new()->Self{
        spawn_ban_checker();
        let licensed=is_licensed();
        let shared=Arc::new(Shared::new(20));
        let hotkey=Arc::new(Mutex::new(HotKey::Keyboard(Key::KeyF)));
        let binding_mouse: Arc<AtomicU64> = Arc::new(AtomicU64::new(0));

        // click thread
        {
            let s=Arc::clone(&shared);
            thread::Builder::new().name("click".into()).stack_size(32*1024).spawn(move||{
                #[cfg(windows)] unsafe {
                    // ── Maximum timer resolution (100ns) ──────────────────────
                    let mut cur: u32 = 0;
                    NtSetTimerResolution(1, 1, &mut cur); // 1 = 100ns, finest possible
                    winapi::um::timeapi::timeBeginPeriod(1);

                    // ── REALTIME process priority ─────────────────────────────
                    // This is the most impactful single change — makes Windows
                    // treat our process above everything except kernel threads
                    winapi::um::processthreadsapi::SetPriorityClass(
                        winapi::um::processthreadsapi::GetCurrentProcess(),
                        winapi::um::winbase::REALTIME_PRIORITY_CLASS);

                    // ── TIME_CRITICAL thread within REALTIME process ───────────
                    winapi::um::processthreadsapi::SetThreadPriority(
                        winapi::um::processthreadsapi::GetCurrentThread(),
                        winapi::um::winbase::THREAD_PRIORITY_TIME_CRITICAL as i32);

                    // ── Pin to core 2 (core 0 handles system IRQs on most PCs) ─
                    winapi::um::winbase::SetThreadAffinityMask(
                        winapi::um::processthreadsapi::GetCurrentThread(), 0b100);

                    // ── MMCSS: protect thread from CPU starvation under load ───
                    let task_name = b"Games
