package protocol

import (
	"encoding/json"
	"strings"
	"testing"
	"unicode/utf8"
)

// FuzzScanTopLevelString holds the general envelope type scanner
// (scanTopLevelString) to its "never-wrong" contract against encoding/json.
// This complements FuzzChunkFrameDecode, which targets only the concrete
// inference_response_chunk fast path; this target exercises the generic
// top-level "type" lookup that ProviderMessage.UnmarshalJSON runs for every
// non-chunk frame.
//
// Two properties, and only these two, are enforced:
//
//  1. No panic on any input — malformed JSON, truncation, invalid UTF-8, or
//     hostile nesting. The scanner is a byte walk that the read loop feeds
//     untrusted provider bytes; it must never crash.
//
//  2. Never-wrong: whenever the scanner reports a value AND encoding/json
//     accepts the same bytes as a JSON document, the scanner's value must equal
//     the reference envelope's Type. The comparison is gated on the scanned
//     bytes being valid UTF-8: the scanner returns the raw bytes between the
//     quotes, whereas encoding/json substitutes U+FFFD for invalid UTF-8 in a
//     string value, so a byte comparison would spuriously differ there. That
//     divergence is not a scanner defect — production runs the same
//     UnmarshalJSON on both paths (a type string with invalid UTF-8 matches no
//     known type constant and errors identically) — so it is excluded rather
//     than asserted. The scanner bails on every backslash escape, so invalid
//     UTF-8 is the *only* way its raw value can differ from encoding/json's
//     decoded string; gating on utf8.Valid makes the invariant exact.
//
// Deliberately NOT enforced: that the scanner is a full JSON validator. It
// accepts inputs encoding/json rejects (trailing garbage after a closed
// object, a second document, invalid UTF-8) because the caller re-runs the
// concrete json.Unmarshal, which reports the same error the old envelope pass
// did. Requiring the partial scanner to reject those would test a property the
// production code never relies on. The assertion is therefore only made when
// the reference decode succeeds.
func FuzzScanTopLevelString(f *testing.F) {
	// Seeds: valid frames the scanner should read on the fast path.
	f.Add([]byte(`{"type":"heartbeat","status":"idle"}`))
	f.Add([]byte(`{"type":"register"}`))
	f.Add([]byte(`{"stats":{"type":"decoy"},"models":[{"type":"inner"}],"type":"register"}`))
	f.Add([]byte(`{"a":1,"b":-2.5,"c":1e10,"e":true,"g":null,"type":"heartbeat"}`))
	f.Add([]byte(" \t\r\n{ \"a\" : 1 ,\n\"type\" :\r\"heartbeat\" }"))
	f.Add([]byte(`{"note":"say \"hi\" \\ done","type":"heartbeat"}`))

	// Seeds: absent / null / duplicate / case-variant type fields.
	f.Add([]byte(`{"status":"idle"}`))
	f.Add([]byte(`{"type":null}`))
	f.Add([]byte(`{"type":123}`))
	f.Add([]byte(`{"type":"first","type":"second"}`))
	f.Add([]byte(`{"type":"first","Type":"second"}`))
	f.Add([]byte(`{"Type":"heartbeat"}`))

	// Seeds: escaped keys/values the scanner defers.
	f.Add([]byte(`{"note":1,"type":"heartbeat"}`))
	f.Add([]byte(`{"type":"heartbeat"}`))

	// Seeds: malformed / truncated / non-object / trailing-garbage / two docs.
	f.Add([]byte(``))
	f.Add([]byte(`{`))
	f.Add([]byte(`{"type":`))
	f.Add([]byte(`{"type":"heartb`))
	f.Add([]byte(`["type","heartbeat"]`))
	f.Add([]byte(`null`))
	f.Add([]byte(`{"type":"heartbeat"}trailing`))
	f.Add([]byte(`{"type":"heartbeat"} {"type":"register"}`))

	// Seeds: invalid UTF-8 inside key and value.
	f.Add([]byte("{\"type\":\"x\xff\"}"))
	f.Add([]byte("{\"type\":\"\xc3\x28\"}"))
	f.Add([]byte("{\"ty\xffpe\":\"heartbeat\"}"))

	// Seeds: bounded deep nesting (iterative in the scanner; encoding/json has
	// its own depth guard). Kept well under the length cap and small enough to
	// stay inside encoding/json's max-depth budget.
	f.Add([]byte(strings.Repeat(`{"a":`, 200) + `1` + strings.Repeat(`}`, 200) + `,"type":"x"`))

	f.Fuzz(func(t *testing.T, data []byte) {
		// Bound the body: skip large inputs so a single execution stays cheap
		// and the corpus cannot grow pathological.
		if len(data) > 4096 {
			t.Skip()
		}

		value, ok := scanTopLevelString(data, "type")

		if ok {
			var envelope struct {
				Type string `json:"type"`
			}
			// Only compare when the bytes are a JSON document encoding/json
			// accepts: the scanner is intentionally not a full validator.
			if err := json.Unmarshal(data, &envelope); err == nil && utf8.Valid([]byte(value)) {
				if envelope.Type != value {
					t.Fatalf("scanner returned %q but envelope decode returned %q for %q",
						value, envelope.Type, data)
				}
			}
		}

		// The read loop also drives the full decode over the same untrusted
		// bytes; exercise it here purely for the no-panic guarantee. Errors are
		// expected and ignored — behavioral equivalence with json.Unmarshal is
		// covered by the golden and chunk-fuzz suites.
		var pm ProviderMessage
		_ = pm.UnmarshalJSON(data)
	})
}
