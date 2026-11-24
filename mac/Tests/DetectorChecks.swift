import Foundation

@main struct DetectorChecks {
    static func run(
        pulses: [Double], sustained: ClosedRange<Double>? = nil,
        brightness: Double = 0.7, resetAt: Double? = nil,
        suppressAt: Double? = nil, suppressUntil: Double = 0,
        end: Double = 8
    ) -> [Double] {
        var detector = DoubleClapDetector()
        detector.reset(at: 0, settleFor: 0.5, keepCooldown: false)
        var triggers: [Double] = []
        for tick in 0...Int(end * 100) {
            let time = Double(tick) / 100
            if let resetAt, abs(time - resetAt) < 0.001 {
                detector.reset(at: time, settleFor: 0, keepCooldown: true)
            }
            if let suppressAt, abs(time - suppressAt) < 0.001 {
                detector.suppress(until: suppressUntil)
            }
            let impulse = pulses.contains { abs(time - $0) < 0.005 }
            let loud = impulse || (sustained?.contains(time) ?? false)
            if detector.process(rms: loud ? 0.02 : 0.0003, peak: loud ? 0.10 : 0.0007,
                                time: time, highFrequencyRatio: loud ? brightness : 0.2) {
                triggers.append(time)
            }
        }
        return triggers
    }
    /// A room with the TV or music on: steady, dark background sound, with strikes on top of it.
    static func noisy(pulses: [Double], rms: Double, peak: Double, brightness: Double, end: Double = 6) -> Int {
        var detector = DoubleClapDetector()
        detector.reset(at: 0, settleFor: 0.5, keepCooldown: false)
        var triggers = 0
        for tick in 0...Int(end * 100) {
            let time = Double(tick) / 100
            let hit = pulses.contains { abs(time - $0) < 0.005 }
            let wobble = 0.03 + 0.004 * sin(time * 9)
            if detector.process(rms: hit ? rms : wobble, peak: hit ? peak : wobble * 2.2,
                                time: time, highFrequencyRatio: hit ? brightness : 0.08) { triggers += 1 }
        }
        return triggers
    }
    static func main() {
        let checks: [(String, Bool)] = [
            ("double clap", run(pulses: [1.0, 1.35]).count == 1),
            ("double snap: softer, darker, slower", run(pulses: [1.0, 1.85], brightness: 0.25).count == 1),
            ("fires once the pair is confirmed", (run(pulses: [1.0, 1.35]).first ?? 0) > 1.6),
            ("three snaps are not two", run(pulses: [1.0, 1.4, 1.8]).isEmpty),
            ("typing run", run(pulses: [1.0, 1.25, 1.5, 1.75, 2.0, 2.25]).isEmpty),
            ("strike right before the pair", run(pulses: [0.8, 1.0, 1.35]).isEmpty),
            ("single clap", run(pulses: [1.0]).isEmpty),
            ("too rapid", run(pulses: [1.0, 1.10]).isEmpty),
            ("too distant", run(pulses: [1.0, 2.2]).isEmpty),
            ("low-frequency impulses", run(pulses: [1.0, 1.35], brightness: 0.05).isEmpty),
            ("sustained sound", run(pulses: [], sustained: 1.0...1.4).isEmpty),
            ("cooldown and rearm", run(pulses: [1.0, 1.35, 2.0, 2.35, 7.0, 7.35], end: 9).count == 2),
            ("reset drops first clap", run(pulses: [1.0, 1.35], resetAt: 1.20).isEmpty),
            ("playback suppression drops first clap", run(pulses: [1.0, 1.35], suppressAt: 1.20, suppressUntil: 3).isEmpty),
            ("silence", run(pulses: []).isEmpty),
            // Values from the log of 13/11/2026 (two real snaps dropped over a loud room).
            ("double snap over the TV", noisy(pulses: [3.0, 3.45], rms: 0.066, peak: 0.48, brightness: 0.87) == 1),
            ("voice bursts over the TV are not snaps", noisy(pulses: [3.0, 3.45], rms: 0.075, peak: 0.19, brightness: 0.10) == 0),
            ("one snap over the TV is not two", noisy(pulses: [3.0], rms: 0.066, peak: 0.48, brightness: 0.87) == 0),
        ]
        for (name, ok) in checks {
            guard ok else { fatalError("FAIL: \(name)") }
            print("PASS: \(name)")
        }
        print("\(checks.count) synthetic checks passed. No capture/playback/network.")
    }
}
