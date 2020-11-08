use std::env;
use std::fs;
use std::process;
use std::str;

use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug)]
struct NemFile {
    version: String,
    cmds: Vec<Cmd>,
    #[serde(skip)]
    file_name: String,
}

impl NemFile {
    fn codes(&self) -> Vec<&String> {
        self.cmds.iter().map(|c| &c.code).collect()
    }

    fn add_cmd(&mut self, cmd: Cmd) {
        self.cmds.push(cmd);
    }

    fn write_to_file(&self) {
        let s = toml::to_string(&self).unwrap();
        fs::write(&self.file_name, s).expect("Unable to write to file");
    }
}

#[derive(Serialize, Deserialize, Debug)]
struct Cmd {
    cmd: String,
    code: String,
    desc: String,
}

fn mnemonic(v: Vec<String>, existing: Vec<&String>) -> String {
    let mut n = v.iter().map(|v| v.chars().nth(0).unwrap()).collect();

    while existing.iter().any(|s| **s == n) {
        n = n + "1";
    }
    n
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let contents = fs::read_to_string(".nem.toml").expect("Couldn't open file.");
    let mut nem_file: NemFile = toml::from_str(&contents).expect("Couldn't parse toml file");
    nem_file.file_name = String::from(".nem.toml");

    if args.len() < 2 {
        println!("todo");
        return;
    }

    match args[1].as_str() {
        "/cc" => {
            if args.len() < 3 {
                println!("please specify a command to create");
                process::exit(1);
            }
            let nem = mnemonic(args[2..].to_vec(), nem_file.codes());
            nem_file.add_cmd(Cmd {
                cmd: args[2..].join(" "),
                code: nem,
                desc: "".to_string(),
            });
            nem_file.write_to_file();
        }
        "/ce" => {
            if args.len() != 4 {
                println!(
                    "unexpected number of arguments. expected <existing code> <replacement code>"
                );
                process::exit(1);
            }
        }
        "/cl" => {
            for cmd in nem_file.cmds {
                println!("{}\t{}", cmd.code, cmd.cmd);
            }
            process::exit(0);
        }
        _ => {
            let cmd: Vec<&str> = match nem_file.cmds.iter().find(|&c| c.code == args[1]) {
                Some(cmd) => cmd.cmd.split(" ").collect(),
                None => vec![&args[0], "/h"],
            };
            let exec = process::Command::new("sh")
                .arg("-c")
                .arg(format!("which {}", cmd[0]))
                .output()
                .expect("Failed to look up command.");
            let location = str::from_utf8(&exec.stdout)
                .expect("Couldn't convert to string.")
                .trim();

            let cmd_args = cmd[1..cmd.len()].iter().map(|s| s.to_string());
            let c = process::Command::new(&location)
                .args(cmd_args)
                .spawn()
                .expect("oops")
                .wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_commands() {}
}
