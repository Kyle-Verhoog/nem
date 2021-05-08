use std::env;
use std::fs;
use std::process;
use std::str;

use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug)]
struct NemFile {
    version: String,
    #[serde(rename = "cmds")]
    entries: Vec<Entry>,
    #[serde(skip)]
    file_name: String,
}

impl NemFile {
    fn codes(&self) -> Vec<&String> {
        self.entries.iter().map(|c| &c.code).collect()
    }

    fn sort(&mut self) {
        self.entries.sort_by(|a, b| a.cmd.cmp(&b.cmd))
    }

    fn add_entry(&mut self, entry: Entry) {
        self.entries.push(entry);
    }

    fn rm_entry(&mut self, code: &String) -> Option<Entry> {
        self.entries
            .iter()
            .position(|c| c.code == *code)
            .map(|index| self.entries.remove(index))
    }

    fn find_entry_by_code(&self, code: &String) -> Option<&Entry> {
        self.entries.iter().find(|c| c.code == *code)
    }

    fn find_entry_by_code_mut(&mut self, code: &String) -> Option<&mut Entry> {
        self.entries.iter_mut().find(|c| c.code == *code)
    }

    fn edit_code(&mut self, old_code: &String, new_code: &String) -> Option<&Entry> {
        self.find_entry_by_code_mut(old_code).map(|entry| {
            entry.code = new_code.clone();
            &*entry
        })
    }

    fn write_to_file(&self) {
        let s = toml::to_string(&self).unwrap();
        fs::write(&self.file_name, s).expect("unable to write to file");
    }
}

#[derive(Serialize, Deserialize, Debug)]
struct Entry {
    cmd: String,
    code: String,
    desc: String,
}

#[derive(Debug)]
struct NemFiles {
    nem_files: Vec<NemFile>,
}

impl NemFiles {
    fn cur_nem_file_mut(&mut self) -> &mut NemFile {
        &mut self.nem_files[0]
    }

    fn codes(&self) -> Vec<&String> {
        self.nem_files.iter().flat_map(NemFile::codes).collect()
    }

    fn sort(&mut self) {
        for nem_file in &mut self.nem_files {
            nem_file.sort();
        }
    }

    fn add_entry(&mut self, entry: Entry) {
        let nem_file = self.cur_nem_file_mut();
        nem_file.add_entry(entry);
    }

    fn rm_entry(&mut self, code: &String) -> Option<Entry> {
        self.nem_files
            .iter_mut()
            .find(|nf| nf.find_entry_by_code(code).is_some())
            .and_then(|nf| nf.rm_entry(code))
    }

    fn find_entry_by_code(&self, code: &String) -> Option<&Entry> {
        self.nem_files
            .iter()
            .filter_map(|nf| nf.find_entry_by_code(code))
            .next()
    }

    fn edit_code(&mut self, old_code: &String, new_code: &String) -> Option<&Entry> {
        self.nem_files
            .iter_mut()
            .find(|nf| nf.find_entry_by_code(old_code).is_some())
            .and_then(|nf| nf.edit_code(old_code, new_code))
    }

    fn write(&mut self) {
        for nem_file in &self.nem_files {
            nem_file.write_to_file()
        }
    }
}

fn mnemonic(v: &[String], existing: Vec<&String>) -> String {
    let mut n = v
        .iter()
        .filter_map(|w| w.chars().filter(|c| *c != '-').next())
        .collect();

    while existing.iter().any(|s| **s == n) {
        n = n + "1";
    }
    n
}

fn main() {
    let mut args: Vec<_> = env::args().collect();

    let mut nem_files = NemFiles {
        nem_files: Vec::new(),
    };
    let mut path_buf = env::current_dir().expect("couldn't get CWD");
    loop {
        path_buf.push(".nem.toml");
        if path_buf.exists() {
            match fs::read_to_string(&path_buf) {
                Ok(contents) => {
                    let mut nem_file: NemFile =
                        toml::from_str(&contents).expect("couldn't parse toml file");
                    nem_file.file_name = String::from(path_buf.to_str().unwrap());
                    nem_file.sort();
                    nem_files.nem_files.push(nem_file);
                }
                Err(err) => {
                    eprintln!("{}", err);
                }
            }
        }
        path_buf.pop();

        if !path_buf.pop() {
            break;
        }
    }

    if args.len() < 2 {
        args.push("/cl".to_string());
    }

    match args[1].as_str() {
        "/cc" => {
            if args.len() < 3 {
                eprintln!("please specify a command to create");
                process::exit(1);
            }
            let nem = mnemonic(&args[2..], nem_files.codes());
            nem_files.add_entry(Entry {
                cmd: args[2..].join(" "),
                code: nem,
                desc: "".to_string(),
            });
        }
        "/ce" => {
            if args.len() != 4 {
                eprintln!("unexpected arguments. expected <existing code> <replacement code>");
                process::exit(1);
            }
            if let Some(entry) = nem_files.find_entry_by_code(&args[3]) {
                eprintln!(
                    "collision: code '{}' already exists for `{}`",
                    &args[3], entry.cmd
                );
                process::exit(1);
            } else {
                let entry = nem_files.edit_code(&args[2], &args[3]).unwrap();
                println!(
                    "edited code for `{}` from '{}' to '{}'",
                    &entry.cmd, &args[2], &entry.code
                );
            }
        }
        "/cl" => {
            for nem_file in nem_files.nem_files.iter().rev() {
                println!("file: {}", &nem_file.file_name);
                for entry in &nem_file.entries {
                    println!("\t{}\t{}", entry.code, entry.cmd);
                }
            }
        }
        "/cr" => {
            if args.len() != 3 {
                eprintln!("unexpected number of arguments. expected <code to remove>");
                process::exit(1);
            }
            if let Some(entry) = nem_files.rm_entry(&args[2]) {
                println!("removed command `{}` for code '{}'", entry.cmd, &args[2]);
            } else {
                eprintln!("no command for code '{}' found", &args[2]);
                process::exit(1);
            }
        }
        _ => {
            let cmd = nem_files
                .find_entry_by_code(&args[1])
                .map(|cmd| cmd.cmd.split(" ").collect::<Vec<&str>>())
                .unwrap_or_else(|| vec![&args[0], "/cl"]);
            let exec = process::Command::new("sh")
                .arg("-c")
                .arg(format!("which {}", cmd[0]))
                .output()
                .expect("failed to look up command");
            let location = str::from_utf8(&exec.stdout)
                .expect("couldn't convert to string")
                .trim();

            let _ = process::Command::new(&location)
                .args(&cmd[1..])
                .args(&args[2..])
                .spawn()
                .expect("failed to execute command")
                .wait();
        }
    }
    nem_files.sort();
    nem_files.write()
}
