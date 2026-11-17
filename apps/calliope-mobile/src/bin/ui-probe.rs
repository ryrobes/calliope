//! Private structural-test client, invoked with `adb shell run-as`.
use std::{
    io::{self, BufRead, Read, Write},
    os::unix::net::UnixStream,
    time::Duration,
};
fn main() -> io::Result<()> {
    let mut request = String::new();
    io::stdin().lock().take(2_097_152).read_line(&mut request)?;
    let mut socket = UnixStream::connect("files/ui-probe.sock")?;
    socket.set_read_timeout(Some(Duration::from_secs(8)))?;
    socket.set_write_timeout(Some(Duration::from_secs(8)))?;
    writeln!(socket, "{}", request.trim())?;
    let mut response = String::new();
    io::BufReader::new(socket)
        .take(8_388_608)
        .read_line(&mut response)?;
    print!("{response}");
    Ok(())
}
