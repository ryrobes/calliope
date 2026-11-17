import AVFoundation
import Foundation

/// Device I/O only; providers, transcription and synthesis stay on Calliope.
/// Blocking read/write/finish are called by Rust's audio worker, never UIKit.
final class NativeAudio {
    static let shared = NativeAudio()
    private let control = DispatchQueue(label: "ai.rvbbit.calliope.audio")
    private let state = NSCondition()
    private var active: Int64 = -1
    private var cancelledThrough: Int64 = 0
    private var inForeground = false
    private var recording = false
    private var playing = false
    private var failure: String?
    private var pcm = Data()
    private var pendingFrames = 0
    private var rate = 16000.0
    // Only accessed on control, except the engine's own callbacks.
    private var engine: AVAudioEngine?
    private var player: AVAudioPlayerNode?

    private init() {
        NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: nil) { [weak self] _ in
            self?.control.async { self?.closeAll("Audio was interrupted. Tap Dictate or Read again.") }
        }
        NotificationCenter.default.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: nil) { [weak self] notification in
            if notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue {
                self?.control.async { self?.closeAll("Audio device disconnected.") }
            }
        }
        NotificationCenter.default.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: nil, queue: nil) { [weak self] _ in
            self?.control.async { self?.closeAll("Audio restarted. Please try again.") }
        }
    }
    func foreground(_ value: Bool) {
        state.lock(); inForeground = value; state.broadcast(); state.unlock()
        if !value { control.async { self.closeAll("Return to Calliope to use voice.") } }
    }
    private func error(_ message: String) -> NSError {
        NSError(domain: "CalliopeAudio", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }
    private func permission() throws {
        switch AVAudioSession.sharedInstance().recordPermission {
        case .granted: return
        case .undetermined:
            DispatchQueue.main.async { AVAudioSession.sharedInstance().requestRecordPermission { _ in } }
            throw error("Allow microphone access, then tap Dictate again.")
        default: throw error("Enable Microphone for Calliope in iOS Settings, then tap Dictate again.")
        }
    }
    // Called only on control. Invalidate before stopping callbacks to avoid deadlocks.
    private func closeAll(_ message: String? = nil) {
        state.lock()
        let wasRecording = recording
        if active >= 0 { cancelledThrough = max(cancelledThrough, active) }
        active = -1; recording = false; playing = false
        failure = message; pcm.removeAll(); pendingFrames = 0
        state.broadcast(); state.unlock()
        if wasRecording { engine?.inputNode.removeTap(onBus: 0) }
        player?.stop(); engine?.stop(); engine = nil; player = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
    private func begin(_ id: Int64, record: Bool, sampleRate: Double) throws {
        state.lock(); let allowed = inForeground && id > cancelledThrough; state.unlock()
        guard allowed else { throw error("Audio operation cancelled. Return to Calliope and try again.") }
        closeAll()
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(record ? .playAndRecord : .playback, mode: record ? .measurement : .spokenAudio,
                                options: record ? [.defaultToSpeaker, .allowBluetooth] : [])
        try session.setActive(true)
        let audio = AVAudioEngine()
        engine = audio
        state.lock(); active = id; failure = nil; recording = record; playing = !record; rate = sampleRate; state.unlock()
        do {
            if record {
                let source = audio.inputNode
                let input = source.outputFormat(forBus: 0)
                let converter = try CapturePCM(input: input)
                source.installTap(onBus: 0, bufferSize: 2048, format: input) { [weak self] buffer, _ in
                    guard let self else { return }
                    let result = Result { try converter.convert(buffer) }
                    self.state.lock(); defer { self.state.unlock() }
                    guard self.active == id, self.recording else { return }
                    switch result {
                    case .failure(let error): self.failure = error.localizedDescription
                    case .success(let bytes):
                        // Rust drains every 100 ms; reject a stalled consumer instead of growing forever.
                        if self.pcm.count + bytes.count > 320000 { self.failure = "Microphone buffer overrun. Please try again." }
                        else { self.pcm.append(bytes) }
                    }
                    self.state.broadcast()
                }
            } else {
                guard let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 1) else { throw error("Speaker format unavailable.") }
                let node = AVAudioPlayerNode(); player = node
                audio.attach(node); audio.connect(node, to: audio.mainMixerNode, format: format)
            }
            audio.prepare(); try audio.start(); player?.play()
        } catch { closeAll(); throw error }
    }
    func command(_ request: [String: Any]) throws -> [String: Any] {
        let id = (request["id"] as? NSNumber)?.int64Value ?? 0
        switch request["action"] as? String {
        case "permission": try permission()
        case "status":
            state.lock(); defer { state.unlock() }
            return ["foreground": inForeground, "recording": recording, "playing": playing,
                    "session": active, "queued_frames": pendingFrames, "buffered_bytes": pcm.count,
                    "microphone": AVAudioSession.sharedInstance().recordPermission == .granted]
        case "record":
            try permission()
            try control.sync { try begin(id, record: true, sampleRate: 16000) }
        case "play":
            let sampleRate = (request["rate"] as? NSNumber)?.doubleValue ?? 0
            guard (8000...96000).contains(sampleRate) else { throw error("Invalid playback sample rate") }
            try control.sync { try begin(id, record: false, sampleRate: sampleRate) }
        case "read":
            state.lock(); defer { state.unlock() }
            let deadline = Date().addingTimeInterval(5)
            while active == id && recording && pcm.isEmpty && failure == nil {
                if !state.wait(until: deadline) { throw error("The microphone is not producing audio.") }
            }
            if let failure { throw error(failure) }
            let data = Data(pcm.prefix(3200)); pcm.removeFirst(data.count)
            return ["data": data.base64EncodedString()]
        case "write":
            guard let encoded = request["data"] as? String, let bytes = Data(base64Encoded: encoded),
                  bytes.count <= 131072, bytes.count % 2 == 0 else { throw error("Invalid audio frame") }
            // Bound scheduled audio to about two seconds, with Stop able to wake us.
            state.lock()
            let deadline = Date().addingTimeInterval(10)
            while active == id && playing && Double(pendingFrames) > rate * 2 {
                if !state.wait(until: deadline) { state.unlock(); throw error("Speaker stopped responding.") }
            }
            let allowed = active == id && playing
            state.unlock()
            guard allowed else { throw error("Playback stopped") }
            try control.sync {
                state.lock(); let valid = active == id && playing; state.unlock()
                guard valid, let player, let format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1),
                      let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(bytes.count / 2)) else { throw error("Playback stopped") }
                buffer.frameLength = buffer.frameCapacity
                bytes.withUnsafeBytes { raw in
                    let samples = raw.bindMemory(to: UInt8.self)
                    for i in 0..<Int(buffer.frameLength) {
                        buffer.floatChannelData![0][i] = Float(Int16(bitPattern: UInt16(samples[i * 2]) | UInt16(samples[i * 2 + 1]) << 8)) / 32768
                    }
                }
                let frames = Int(buffer.frameLength)
                state.lock(); pendingFrames += frames; state.unlock()
                player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                    guard let self else { return }
                    self.state.lock()
                    if self.active == id { self.pendingFrames = max(0, self.pendingFrames - frames) }
                    self.state.broadcast(); self.state.unlock()
                }
            }
        case "finish":
            state.lock()
            let deadline = Date().addingTimeInterval(10)
            while active == id && pendingFrames > 0 {
                if !state.wait(until: deadline) { state.unlock(); throw error("Speaker did not finish playback") }
            }
            let completed = active == id && playing
            state.unlock()
            guard completed else { throw error("Playback stopped") }
            control.sync { state.lock(); let matches = active == id; state.unlock(); if matches { closeAll() } }
        case "stop":
            control.sync {
                state.lock(); cancelledThrough = max(cancelledThrough, id); let matches = active == id; state.unlock()
                if matches { closeAll() }
            }
        default: throw error("Unknown audio operation")
        }
        return ["ok": true]
    }
}
