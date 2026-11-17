import AVFoundation

/// Stateful conversion of device-rate mono/stereo capture to Calliope's PCM contract.
/// Keep one converter for the whole recording, preserving its resampling phase.
final class CapturePCM {
    private let converter: AVAudioConverter
    private let output: AVAudioFormat
    private let inputRate: Double
    init(input: AVAudioFormat) throws {
        guard input.sampleRate > 0, input.channelCount > 0,
              let output = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true),
              let converter = AVAudioConverter(from: input, to: output) else {
            throw NSError(domain: "CalliopeAudio", code: 2, userInfo: [NSLocalizedDescriptionKey: "Microphone format unavailable. Check the device’s audio input."])
        }
        self.converter = converter; self.output = output; inputRate = input.sampleRate
    }
    func convert(_ buffer: AVAudioPCMBuffer) throws -> Data {
        let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * 16000 / inputRate) + 16)
        guard let converted = AVAudioPCMBuffer(pcmFormat: output, frameCapacity: capacity) else { return Data() }
        var supplied = false
        var conversionError: NSError?
        converter.convert(to: converted, error: &conversionError) { _, status in
            if supplied { status.pointee = .noDataNow; return nil }
            supplied = true; status.pointee = .haveData; return buffer
        }
        if let conversionError { throw conversionError }
        guard let samples = converted.int16ChannelData?[0] else { return Data() }
        return Data(bytes: samples, count: Int(converted.frameLength) * 2)
    }
}
