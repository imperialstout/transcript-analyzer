// calendar_fetch.swift — fetch calendar events via EventKit
// Compile once: swiftc bin/calendar_fetch.swift -o bin/calendar_fetch
// Output: JSON array of event objects to stdout
//
// Usage:
//   calendar_fetch                          # today (default)
//   calendar_fetch --start 2026-10-13 --end 2026-10-17   # Mon–Fri inclusive

import EventKit
import Foundation

// Parse --start / --end args (YYYY-MM-DD, inclusive)
var startArg: String? = nil
var endArg: String? = nil
var args = CommandLine.arguments.dropFirst()
var iter = args.makeIterator()
while let a = iter.next() {
    if a == "--start", let v = iter.next() { startArg = v }
    else if a == "--end",   let v = iter.next() { endArg   = v }
}

let cal = Calendar.current
let dateFmt = DateFormatter()
dateFmt.dateFormat = "yyyy-MM-dd"

func parseDate(_ s: String, hour: Int) -> Date? {
    guard let d = dateFmt.date(from: s) else { return nil }
    return cal.date(bySettingHour: hour, minute: 0, second: 0, of: d)
}

let store = EKEventStore()
let sema = DispatchSemaphore(value: 0)

store.requestFullAccessToEvents { granted, error in
    guard granted else {
        let msg = error?.localizedDescription ?? "permission denied"
        fputs("CALENDAR_ERROR: \(msg)\n", stderr)
        sema.signal()
        return
    }

    let startDate: Date
    let endDate: Date

    if let s = startArg, let parsedStart = parseDate(s, hour: 0) {
        startDate = parsedStart
    } else {
        var comps = cal.dateComponents([.year, .month, .day], from: Date())
        comps.hour = 0; comps.minute = 0; comps.second = 0
        startDate = cal.date(from: comps)!
    }

    if let e = endArg, let parsedEnd = parseDate(e, hour: 0) {
        // +1 day so the end date is inclusive
        endDate = cal.date(byAdding: .day, value: 1, to: parsedEnd)!
    } else {
        endDate = cal.date(byAdding: .day, value: 1, to: startDate)!
    }

    let pred = store.predicateForEvents(withStart: startDate, end: endDate, calendars: nil)
    let events = store.events(matching: pred)

    var output: [[String: Any]] = []
    let timeFmt = DateFormatter()
    timeFmt.dateFormat = "HH:mm"
    let isoFmt = ISO8601DateFormatter()

    for ev in events.sorted(by: { $0.startDate < $1.startDate }) {
        var attendeeNames: [String] = []
        if let attendees = ev.attendees {
            attendeeNames = attendees.compactMap { $0.name }.filter { !$0.isEmpty }
        }
        output.append([
            "title":     ev.title ?? "",
            "start":     timeFmt.string(from: ev.startDate),
            "end":       timeFmt.string(from: ev.endDate),
            "start_iso": isoFmt.string(from: ev.startDate),
            "end_iso":   isoFmt.string(from: ev.endDate),
            "calendar":  ev.calendar.title,
            "all_day":   ev.isAllDay,
            "attendees": attendeeNames,
            "location":  ev.location ?? "",
            "url":       ev.url?.absoluteString ?? "",
        ])
    }

    if let data = try? JSONSerialization.data(withJSONObject: output, options: .prettyPrinted),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        print("[]")
    }
    sema.signal()
}

sema.wait()
