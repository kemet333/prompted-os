// Offline, streaming Apple Health XML importer. Only allowlisted summaries leave this process.
// Raw exports, clinical records, identifiers and location data are never retained.
import Foundation
#if canImport(FoundationXML)
import FoundationXML
#endif

final class HealthReader: NSObject, XMLParserDelegate {
    var validRoot = false, failed = false, depth = 0, records = 0, duplicates = 0
    var exportedAt: String? = nil
    var days: [String: [String: Any]] = [:]
    var metrics: [String: (Date, [String: Any])] = [:]
    var workouts: [String: [String: Any]] = [:]
    let dates = DateFormatter(), iso = ISO8601DateFormatter()
    override init() {
        super.init()
        dates.locale = Locale(identifier: "en_US_POSIX")
        dates.dateFormat = "yyyy-MM-dd HH:mm:ss Z"
        dates.isLenient = false
    }
    func date(_ s: String?) -> Date? { guard let s else { return nil }; return dates.date(from: s) ?? iso.date(from: s) }
    func day(_ s: String?) -> String? {
        guard let s, s.range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) != nil,
              let d = dates.date(from: s + " 12:00:00 +0000"),
              String(iso.string(from: d).prefix(10)) == s else { return nil }
        return s
    }
    func num(_ s: String?) -> Double? { guard let s, let n = Double(s), n.isFinite, n >= 0 else { return nil }; return n }
    func text(_ s: String?) -> String { String((s ?? "").unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }.prefix(100)) }
    func kcal(_ s: String?, _ unit: String?) -> Double? {
        guard let n = num(s) else { return nil }
        if unit == "kcal" { return n }
        if unit == "kJ" { return n / 4.184 }
        return nil
    }
    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?, qualifiedName: String?, attributes a: [String: String]) {
        depth += 1; records += 1
        if depth > 32 || records > 8_000_000 { failed = true; parser.abortParsing(); return }
        if depth == 1 { validRoot = elementName == "HealthData"; if !validRoot { failed = true; parser.abortParsing() }; return }
        if elementName == "ExportDate", let d = date(a["value"]) { exportedAt = iso.string(from: d) }
        if elementName == "ActivitySummary", let key = day(a["dateComponents"]) {
            var v: [String: Any] = ["date": key]
            v["energy"] = kcal(a["activeEnergyBurned"], a["activeEnergyBurnedUnit"])
            v["energyGoal"] = kcal(a["activeEnergyBurnedGoal"], a["activeEnergyBurnedUnit"])
            v["exercise"] = num(a["appleExerciseTime"]); v["exerciseGoal"] = num(a["appleExerciseTimeGoal"])
            v["stand"] = num(a["appleStandHours"]); v["standGoal"] = num(a["appleStandHoursGoal"])
            days[key] = v
            // Keep at most one year of Apple's own daily summaries; never sum Watch+iPhone records.
            if days.count > 366, let oldest = days.keys.min() { days.removeValue(forKey: oldest) }
        }
        if elementName == "Record", let when = date(a["endDate"]), let value = num(a["value"]) {
            var id = "", unit = "", n = value
            switch (a["type"], a["unit"]) {
            case ("HKQuantityTypeIdentifierRestingHeartRate", "count/min"): id = "restingHeartRate"; unit = "lpm"
            case ("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", "ms"): id = "hrv"; unit = "ms"
            case ("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", "s"): id = "hrv"; unit = "ms"; n *= 1000
            case ("HKQuantityTypeIdentifierBodyMass", "kg"): id = "weight"; unit = "kg"
            case ("HKQuantityTypeIdentifierBodyMass", "lb"): id = "weight"; unit = "kg"; n *= 0.45359237
            default: break
            }
            if !id.isEmpty && when >= (metrics[id]?.0 ?? .distantPast) {
                metrics[id] = (when, ["id": id, "value": n, "unit": unit, "date": iso.string(from: when), "source": text(a["sourceName"])])
            }
        }
        if elementName == "Workout", let start = date(a["startDate"]), let end = date(a["endDate"]), end >= start {
            let activity = text(a["workoutActivityType"]).replacingOccurrences(of: "HKWorkoutActivityType", with: "")
            let key = activity + "|" + iso.string(from: start) + "|" + iso.string(from: end)
            var v: [String: Any] = ["date": iso.string(from: start), "end": iso.string(from: end), "activity": activity, "source": text(a["sourceName"])]
            if let d = num(a["duration"]) {
                if a["durationUnit"] == "min" { v["minutes"] = d }
                if a["durationUnit"] == "s" { v["minutes"] = d / 60 }
                if a["durationUnit"] == "hr" { v["minutes"] = d * 60 }
            }
            if let distance = num(a["totalDistance"]) {
                switch a["totalDistanceUnit"] {
                case "km": v["distanceKm"] = distance
                case "m": v["distanceKm"] = distance / 1000
                case "mi": v["distanceKm"] = distance * 1.609344
                default: break
                }
            }
            v["energy"] = kcal(a["totalEnergyBurned"], a["totalEnergyBurnedUnit"])
            if let old = workouts[key] {
                duplicates += 1
                // Same start/end/activity is one session even if exported by two apps.
                if !(text(a["sourceName"]).lowercased().contains("watch")) { v = old }
            }
            workouts[key] = v
            if workouts.count > 1000, let oldest = workouts.keys.min(by: { String(workouts[$0]?["date"] as? String ?? "") < String(workouts[$1]?["date"] as? String ?? "") }) { workouts.removeValue(forKey: oldest) }
        }
    }
    func parser(_ parser: XMLParser, didEndElement: String, namespaceURI: String?, qualifiedName: String?) { depth -= 1 }
    func parser(_ parser: XMLParser, foundInternalEntityDeclarationWithName: String, value: String?) { failed = true; parser.abortParsing() }
    func parser(_ parser: XMLParser, resolveExternalEntityName: String, systemID: String?) -> Data? { failed = true; parser.abortParsing(); return nil }
    func output() -> [String: Any] {
        ["schema": 1, "exportedAt": exportedAt as Any? ?? NSNull(),
         "days": days.values.sorted { ($0["date"] as! String) > ($1["date"] as! String) },
         "metrics": metrics.values.map { $0.1 }.sorted { ($0["id"] as! String) < ($1["id"] as! String) },
         "workouts": workouts.values.sorted { ($0["date"] as! String) > ($1["date"] as! String) }, "duplicates": duplicates]
    }
}

func run() throws {
    guard CommandLine.arguments.count == 2 else { throw CocoaError(.fileReadInvalidFileName) }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
    guard let size = attrs[.size] as? NSNumber, size.intValue > 0, size.intValue <= 500 * 1024 * 1024 else { throw CocoaError(.fileReadTooLarge) }
    // Apple exports include a DTD; accept it, but reject all entity declarations before XMLParser sees them.
    // Only UTF-8 XML is accepted, preventing alternative encodings from hiding declarations.
    let handle = try FileHandle(forReadingFrom: url); defer { try? handle.close() }
    var tail = Data(), first = true
    while let chunk = try handle.read(upToCount: 65536), !chunk.isEmpty {
        if chunk.contains(0) { throw CocoaError(.fileReadCorruptFile) }
        let joined = tail + chunk
        if first {
            let prefix = String(decoding: chunk.prefix(300), as: UTF8.self).lowercased()
            if prefix.contains("encoding") && !prefix.contains("utf-8") && !prefix.contains("utf8") { throw CocoaError(.fileReadCorruptFile) }
            first = false
        }
        if String(decoding: joined, as: UTF8.self).uppercased().contains("<!ENTITY") { throw CocoaError(.fileReadCorruptFile) }
        tail = Data(joined.suffix(16))
    }
    guard let parser = XMLParser(contentsOf: url) else { throw CocoaError(.fileReadCorruptFile) }
    let reader = HealthReader(); parser.delegate = reader; parser.shouldResolveExternalEntities = false
    guard parser.parse(), reader.validRoot, !reader.failed,
          !reader.days.isEmpty || !reader.metrics.isEmpty || !reader.workouts.isEmpty else { throw CocoaError(.fileReadCorruptFile) }
    let data = try JSONSerialization.data(withJSONObject: reader.output(), options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
}
do { try run() } catch { FileHandle.standardError.write(Data("No se pudo leer la exportación de Salud.\n".utf8)); exit(1) }
