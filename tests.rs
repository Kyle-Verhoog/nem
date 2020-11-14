use std::env;
use std::error;
use std::ffi::OsStr;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::{self, Command};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;
use std::time::Duration;

// Macros and test utils stolen from ripgrep.
// https://github.com/BurntSushi/ripgrep/
#[macro_export]
macro_rules! nemtest {
    ($name:ident, $fun:expr) => {
        #[test]
        fn $name() {
            let (dir, cmd) = setup(stringify!($name));
            $fun(dir, cmd);
        }
    };
}

#[macro_export]
macro_rules! eqnice {
    ($expected:expr, $got:expr) => {
        let expected = &*$expected;
        let got = &*$got;
        if expected != got {
            panic!(
                "
printed outputs differ!
expected:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
{}
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
got:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
{}
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
",
                expected, got
            );
        }
    };
}

static TEST_DIR: &'static str = "nem-tests";
static NEXT_ID: AtomicUsize = AtomicUsize::new(0);

pub fn setup(test_name: &str) -> (Dir, TestCommand) {
    let dir = Dir::new(test_name);
    let cmd = dir.command();
    (dir, cmd)
}

/// Return a recursive listing of all files and directories in the given
/// directory. This is useful for debugging transient and odd failures in
/// integration tests.
fn dir_list<P: AsRef<Path>>(dir: P) -> Vec<String> {
    walkdir::WalkDir::new(dir)
        .follow_links(true)
        .into_iter()
        .map(|result| result.unwrap().path().to_string_lossy().into_owned())
        .collect()
}

fn nice_err<T, E: error::Error>(path: &Path, res: Result<T, E>) -> T {
    match res {
        Ok(t) => t,
        Err(err) => panic!("{}: {:?}", path.display(), err),
    }
}

fn repeat<F: FnMut() -> io::Result<()>>(mut f: F) -> io::Result<()> {
    let mut last_err = None;
    for _ in 0..10 {
        if let Err(err) = f() {
            last_err = Some(err);
            thread::sleep(Duration::from_millis(500));
        } else {
            return Ok(());
        }
    }
    Err(last_err.unwrap())
}

#[derive(Clone, Debug)]
pub struct Dir {
    /// The directory in which this test executable is running.
    root: PathBuf,
    /// The directory in which the test should run. If a test needs to create
    /// files, they should go in here. This directory is also used as the CWD
    /// for any processes created by the test.
    dir: PathBuf,
}

impl Dir {
    /// Create a new test working directory with the given name. The name
    /// does not need to be distinct for each invocation, but should correspond
    /// to a logical grouping of tests.
    pub fn new(name: &str) -> Dir {
        let id = NEXT_ID.fetch_add(1, Ordering::SeqCst);
        let root = env::current_exe()
            .unwrap()
            .parent()
            .expect("executable's directory")
            .to_path_buf();
        let dir = env::temp_dir()
            .join(TEST_DIR)
            .join(name)
            .join(&format!("{}", id));
        if dir.exists() {
            nice_err(&dir, fs::remove_dir_all(&dir));
        }
        nice_err(&dir, repeat(|| fs::create_dir_all(&dir)));
        Dir {
            root: root,
            dir: dir,
        }
    }

    /// Create a new file with the given name and contents in this directory,
    /// or panic on error.
    pub fn create<P: AsRef<Path>>(&self, name: P, contents: &str) {
        self.create_bytes(name, contents.as_bytes());
    }

    /// Try to create a new file with the given name and contents in this
    /// directory.
    #[allow(dead_code)] // unused on Windows
    pub fn try_create<P: AsRef<Path>>(&self, name: P, contents: &str) -> io::Result<()> {
        let path = self.dir.join(name);
        self.try_create_bytes(path, contents.as_bytes())
    }

    /// Create a new file with the given name and size.
    pub fn create_size<P: AsRef<Path>>(&self, name: P, filesize: u64) {
        let path = self.dir.join(name);
        let file = nice_err(&path, File::create(&path));
        nice_err(&path, file.set_len(filesize));
    }

    /// Create a new file with the given name and contents in this directory,
    /// or panic on error.
    pub fn create_bytes<P: AsRef<Path>>(&self, name: P, contents: &[u8]) {
        let path = self.dir.join(&name);
        nice_err(&path, self.try_create_bytes(name, contents));
    }

    /// Try to create a new file with the given name and contents in this
    /// directory.
    pub fn try_create_bytes<P: AsRef<Path>>(&self, name: P, contents: &[u8]) -> io::Result<()> {
        let path = self.dir.join(name);
        let mut file = File::create(path)?;
        file.write_all(contents)?;
        file.flush()
    }

    /// Remove a file with the given name from this directory.
    pub fn remove<P: AsRef<Path>>(&self, name: P) {
        let path = self.dir.join(name);
        nice_err(&path, fs::remove_file(&path));
    }

    /// Create a new directory with the given path (and any directories above
    /// it) inside this directory.
    pub fn create_dir<P: AsRef<Path>>(&self, path: P) {
        let path = self.dir.join(path);
        nice_err(&path, repeat(|| fs::create_dir_all(&path)));
    }

    /// Creates a new command that is set to use the ripgrep executable in
    /// this working directory.
    pub fn command(&self) -> TestCommand {
        let mut cmd = self.bin();
        cmd.current_dir(&self.dir);
        TestCommand {
            dir: self.clone(),
            cmd: cmd,
        }
    }

    pub fn bin(&self) -> process::Command {
        let nem = self.root.join(format!("../n{}", env::consts::EXE_SUFFIX));
        process::Command::new(nem)
    }

    pub fn path(&self) -> &Path {
        &self.dir
    }

    /// Creates a directory symlink to the src with the given target name
    /// in this directory.
    #[cfg(not(windows))]
    pub fn link_dir<S: AsRef<Path>, T: AsRef<Path>>(&self, src: S, target: T) {
        use std::os::unix::fs::symlink;
        let src = self.dir.join(src);
        let target = self.dir.join(target);
        let _ = fs::remove_file(&target);
        nice_err(&target, symlink(&src, &target));
    }

    /// Creates a directory symlink to the src with the given target name
    /// in this directory.
    #[cfg(windows)]
    pub fn link_dir<S: AsRef<Path>, T: AsRef<Path>>(&self, src: S, target: T) {
        use std::os::windows::fs::symlink_dir;
        let src = self.dir.join(src);
        let target = self.dir.join(target);
        let _ = fs::remove_dir(&target);
        nice_err(&target, symlink_dir(&src, &target));
    }

    /// Creates a file symlink to the src with the given target name
    /// in this directory.
    #[cfg(not(windows))]
    pub fn link_file<S: AsRef<Path>, T: AsRef<Path>>(&self, src: S, target: T) {
        self.link_dir(src, target);
    }

    /// Creates a file symlink to the src with the given target name
    /// in this directory.
    #[cfg(windows)]
    #[allow(dead_code)] // unused on Windows
    pub fn link_file<S: AsRef<Path>, T: AsRef<Path>>(&self, src: S, target: T) {
        use std::os::windows::fs::symlink_file;
        let src = self.dir.join(src);
        let target = self.dir.join(target);
        let _ = fs::remove_file(&target);
        nice_err(&target, symlink_file(&src, &target));
    }
}

/// A simple wrapper around a process::Command with some conveniences.
#[derive(Debug)]
pub struct TestCommand {
    /// The dir used to launched this command.
    dir: Dir,
    /// The actual command we use to control the process.
    cmd: Command,
}

impl TestCommand {
    /// Returns a mutable reference to the underlying command.
    pub fn cmd(&mut self) -> &mut Command {
        &mut self.cmd
    }

    /// Add an argument to pass to the command.
    pub fn arg<A: AsRef<OsStr>>(&mut self, arg: A) -> &mut TestCommand {
        self.cmd.arg(arg);
        self
    }

    /// Add any number of arguments to the command.
    pub fn args<I, A>(&mut self, args: I) -> &mut TestCommand
    where
        I: IntoIterator<Item = A>,
        A: AsRef<OsStr>,
    {
        self.cmd.args(args);
        self
    }

    /// Set the working directory for this command.
    ///
    /// Note that this does not need to be called normally, since the creation
    /// of this TestCommand causes its working directory to be set to the
    /// test's directory automatically.
    pub fn current_dir<P: AsRef<Path>>(&mut self, dir: P) -> &mut TestCommand {
        self.cmd.current_dir(dir);
        self
    }

    /// Runs and captures the stdout of the given command.
    pub fn stdout(&mut self) -> String {
        let o = self.output();
        let stdout = String::from_utf8_lossy(&o.stdout);
        match stdout.parse() {
            Ok(t) => t,
            Err(err) => {
                panic!("could not convert from string: {:?}\n\n{}", err, stdout);
            }
        }
    }

    /// Pipe `input` to a command, and collect the output.
    pub fn pipe(&mut self, input: &[u8]) -> String {
        self.cmd.stdin(process::Stdio::piped());
        self.cmd.stdout(process::Stdio::piped());
        self.cmd.stderr(process::Stdio::piped());

        let mut child = self.cmd.spawn().unwrap();

        // Pipe input to child process using a separate thread to avoid
        // risk of deadlock between parent and child process.
        let mut stdin = child.stdin.take().expect("expected standard input");
        let input = input.to_owned();
        let worker = thread::spawn(move || stdin.write_all(&input));

        let output = self.expect_success(child.wait_with_output().unwrap());
        worker.join().unwrap().unwrap();

        let stdout = String::from_utf8_lossy(&output.stdout);
        match stdout.parse() {
            Ok(t) => t,
            Err(err) => {
                panic!("could not convert from string: {:?}\n\n{}", err, stdout);
            }
        }
    }

    /// Gets the output of a command. If the command failed, then this panics.
    pub fn output(&mut self) -> process::Output {
        let output = self.cmd.output().unwrap();
        self.expect_success(output)
    }

    /// Runs the command and asserts that it resulted in an error exit code.
    pub fn assert_err(&mut self) {
        let o = self.cmd.output().unwrap();
        if o.status.success() {
            panic!(
                "\n\n===== {:?} =====\n\
                 command succeeded but expected failure!\
                 \n\ncwd: {}\
                 \n\ndir list: {:?}\
                 \n\nstatus: {}\
                 \n\nstdout: {}\n\nstderr: {}\
                 \n\n=====\n",
                self.cmd,
                self.dir.dir.display(),
                dir_list(&self.dir.dir),
                o.status,
                String::from_utf8_lossy(&o.stdout),
                String::from_utf8_lossy(&o.stderr)
            );
        }
    }

    /// Runs the command and asserts that its exit code matches expected exit
    /// code.
    pub fn assert_exit_code(&mut self, expected_code: i32) {
        let code = self.cmd.output().unwrap().status.code().unwrap();
        assert_eq!(
            expected_code,
            code,
            "\n\n===== {:?} =====\n\
             expected exit code did not match\
             \n\ncwd: {}\
             \n\ndir list: {:?}\
             \n\nexpected: {}\
             \n\nfound: {}\
             \n\n=====\n",
            self.cmd,
            self.dir.dir.display(),
            dir_list(&self.dir.dir),
            expected_code,
            code
        );
    }

    /// Runs the command and asserts that something was printed to stderr.
    pub fn assert_non_empty_stderr(&mut self) {
        let o = self.cmd.output().unwrap();
        if o.status.success() || o.stderr.is_empty() {
            panic!(
                "\n\n===== {:?} =====\n\
                 command succeeded but expected failure!\
                 \n\ncwd: {}\
                 \n\ndir list: {:?}\
                 \n\nstatus: {}\
                 \n\nstdout: {}\n\nstderr: {}\
                 \n\n=====\n",
                self.cmd,
                self.dir.dir.display(),
                dir_list(&self.dir.dir),
                o.status,
                String::from_utf8_lossy(&o.stdout),
                String::from_utf8_lossy(&o.stderr)
            );
        }
    }

    fn expect_success(&self, o: process::Output) -> process::Output {
        if !o.status.success() {
            let suggest = if o.stderr.is_empty() {
                "\n\nDid your search end up with no results?".to_string()
            } else {
                "".to_string()
            };

            panic!(
                "\n\n==========\n\
                    command failed but expected success!\
                    {}\
                    \n\ncommand: {:?}\
                    \n\ncwd: {}\
                    \n\ndir list: {:?}\
                    \n\nstatus: {}\
                    \n\nstdout: {}\
                    \n\nstderr: {}\
                    \n\n==========\n",
                suggest,
                self.cmd,
                self.dir.dir.display(),
                dir_list(&self.dir.dir),
                o.status,
                String::from_utf8_lossy(&o.stdout),
                String::from_utf8_lossy(&o.stderr)
            );
        }
        o
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // nemtest!(no_nem_file, |dir: Dir, mut cmd: TestCommand| {
    //    assert_eq!("", cmd.arg("/cl").stdout());
    // });

    nemtest!(list, |dir: Dir, mut cmd: TestCommand| {
        dir.create(
            ".nem.toml",
            r#"
version = "0.0"

[[cmds]]
cmd = "cargo build --release"
code = "cbr"
desc = ""
"#,
        );
        let expected = format!(
            "file: {}\ncbr\tcargo build --release\n",
            dir.path().join(".nem.toml").to_str().unwrap()
        );
        assert_eq!(expected, cmd.arg("/cl").stdout());
    });

    nemtest!(create_command, |dir: Dir, mut cmd: TestCommand| {
        dir.create(
            ".nem.toml",
            r#"
version = "0.0"
cmds = []
"#,
        );
        assert_eq!("", cmd.args(vec!["/cc", "echo"]).stdout());
    });

    nemtest!(cmd_args, |dir: Dir, mut cmd: TestCommand| {
        dir.create(
            ".nem.toml",
            r#"
version = "0.0"

[[cmds]]
cmd = "echo"
code = "e"
desc = ""
"#,
        );
        let expected = "hello\n";
        assert_eq!(expected, cmd.args(vec!["e", "hello"]).stdout());
    });

    nemtest!(multi_cmd_find, |dir: Dir, mut cmd: TestCommand| {
        dir.create_dir("prj");
        dir.create(
            ".nem.toml",
            r#"
version = "0.0"

[[cmds]]
cmd = "echo root"
code = "e"
desc = ""
"#,
        );
        cmd.arg("e");
        assert_eq!("root\n", cmd.stdout());
        cmd.current_dir(dir.path().join("prj"));
        assert_eq!("root\n", cmd.stdout());

        dir.create(
            "prj/.nem.toml",
            r#"
version = "0.0"

[[cmds]]
cmd = "echo prj"
code = "e"
desc = ""
"#,
        );
        cmd.current_dir(dir.path().join("prj"));
        assert_eq!("prj\n", cmd.stdout());
    });
}
