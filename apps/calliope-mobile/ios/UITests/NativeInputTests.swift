import XCTest

/// The host harness selects a disposable draft field using the structural probe
/// and supplies its observed screen coordinates. Typing and rotation here go
/// through UIKit/XCTest, not the probe's fill command. No business write occurs.
final class NativeInputTests: XCTestCase {
    func testAcceptAppLink() throws {
        guard ProcessInfo.processInfo.environment["CALLIOPE_TEST_LINK"] == "1" else {
            throw XCTSkip("Open a Calliope link in the named Simulator and opt in.")
        }
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let open = springboard.buttons["Open"]
        XCTAssertTrue(open.waitForExistence(timeout: 30), springboard.debugDescription)
        open.tap()
        let app = XCUIApplication(bundleIdentifier: "ai.rvbbit.calliope.mobile")
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 15))
        capture("opened-app-link")
    }

    func testChoosePhoto() throws {
        guard ProcessInfo.processInfo.environment["CALLIOPE_TEST_PHOTO"] == "1" else { throw XCTSkip("Open a disposable attachment draft and opt in.") }
        let app = XCUIApplication(bundleIdentifier: "ai.rvbbit.calliope.mobile")
        app.activate()
        let library = app.buttons["Photo library"]
        continueAfterFailure = false
        if library.waitForExistence(timeout: 2) { library.tap() }
        let photo = app.images.matching(identifier: "PXGGridLayout-Info").firstMatch
        XCTAssertTrue(photo.waitForExistence(timeout: 10), app.debugDescription)
        capture("photo-library")
        // Photos' remote picker exposes image frames but reports no hittable point.
        let frame = photo.frame
        app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: frame.midX, dy: frame.midY)).tap()
        // Single selection dismisses automatically.
        XCTAssertTrue(waitUntil { !photo.exists })
        capture("photo-attached")
    }

    func testDismissPreview() throws {
        guard ProcessInfo.processInfo.environment["CALLIOPE_TEST_PREVIEW"] == "1" else { throw XCTSkip("Open an attachment preview and opt in.") }
        let app = XCUIApplication(bundleIdentifier: "ai.rvbbit.calliope.mobile")
        app.activate()
        let done = app.buttons["Done"]
        XCTAssertTrue(done.waitForExistence(timeout: 10), app.debugDescription)
        capture("attachment-preview")
        done.tap()
    }

    func testScrollCurrentSurface() throws {
        guard ProcessInfo.processInfo.environment["CALLIOPE_TEST_SCROLL"] == "1" else {
            throw XCTSkip("Opt in with CALLIOPE_TEST_SCROLL=1 and the desired surface open.")
        }
        let app = XCUIApplication(bundleIdentifier: "ai.rvbbit.calliope.mobile")
        app.activate()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 15))
        app.swipeUp()
        app.swipeUp()
        capture("surface-after-scroll")
    }

    func testKeyboardRotationAndReturn() throws {
        continueAfterFailure = false
        let environment = ProcessInfo.processInfo.environment
        let point = (environment["CALLIOPE_TEST_POINT"] ?? "").split(separator: ",").compactMap { Double($0) }
        let text = environment["CALLIOPE_TEST_TEXT"] ?? ""
        guard point.count == 2, !text.isEmpty, !text.hasPrefix("$(") else {
            throw XCTSkip("Supply CALLIOPE_TEST_POINT=x,y and CALLIOPE_TEST_TEXT with a disposable draft open.")
        }
        let app = XCUIApplication(bundleIdentifier: "ai.rvbbit.calliope.mobile")
        XCUIDevice.shared.orientation = .portrait
        app.activate()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 15))
        app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: point[0], dy: point[1])).tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 10))
        // A new Simulator can show Apple's first-use QuickPath tutorial.
        if app.buttons["Continue"].exists { app.buttons["Continue"].tap() }
        app.typeText(text)
        capture("portrait-keyboard")
        XCUIDevice.shared.orientation = .landscapeLeft
        XCTAssertTrue(waitUntil { app.frame.width > app.frame.height })
        app.typeText("\nLandscape input checked.")
        capture("landscape-keyboard")
        XCUIDevice.shared.press(.home)
        XCTAssertTrue(app.wait(for: .runningBackground, timeout: 10))
        app.activate()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10))
        XCUIDevice.shared.orientation = .portrait
        XCTAssertTrue(waitUntil { app.frame.height > app.frame.width })
        capture("portrait-return")
    }

    private func waitUntil(_ check: @escaping () -> Bool) -> Bool {
        XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in check() }, object: nil)], timeout: 10) == .completed
    }
    private func capture(_ name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
