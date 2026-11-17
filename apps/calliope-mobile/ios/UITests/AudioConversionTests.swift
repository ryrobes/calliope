import XCTest
import AVFoundation

final class AudioConversionTests: XCTestCase {
    func testDeviceRatesAndStereo() throws {
        for rate in [16000.0, 44100.0, 48000.0] {
            for channels in [AVAudioChannelCount(1), 2] {
                let format = try XCTUnwrap(AVAudioFormat(standardFormatWithSampleRate: rate, channels: channels))
                let converter = try CapturePCM(input: format)
                var pcm = Data()
                var index = 0
                while index < Int(rate) {
                    let count = min(1024, Int(rate) - index)
                    let buffer = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)))
                    buffer.frameLength = buffer.frameCapacity
                    for channel in 0..<Int(channels) {
                        for i in 0..<count { buffer.floatChannelData![channel][i] = Float(sin(Double(index + i) * 2 * .pi * 440 / rate) * 0.4) }
                    }
                    pcm.append(try converter.convert(buffer)); index += count
                }
                XCTAssertEqual(Double(pcm.count), 32000, accuracy: 300, "\(rate) Hz / \(channels) channels")
                XCTAssertEqual(pcm.count % 2, 0)
                XCTAssertTrue(pcm.contains(where: { $0 != 0 }))
            }
        }
    }
}
