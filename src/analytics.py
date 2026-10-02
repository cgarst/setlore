import re
from datetime import datetime, date
from collections import defaultdict, Counter
from typing import List, Dict, Any
from src.config import IGNORED_ARTISTS
from src.musician_tracker import get_effective_band_tenures

def record_has_occurred(rec: Dict[str, Any]) -> bool:
    """Returns True if the concert record has already occurred (date <= today)."""
    today = date.today()
    dt = rec.get("date_obj")
    if dt:
        if isinstance(dt, datetime):
            return dt.date() <= today
        elif isinstance(dt, date):
            return dt <= today
    date_str = rec.get("date") or rec.get("raw_date")
    if date_str:
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%b %d, %Y"):
            try:
                parsed_d = datetime.strptime(str(date_str).strip()[:10], fmt).date()
                return parsed_d <= today
            except ValueError:
                pass
    return True

class ConcertAnalytics:
    def __init__(self, matched_setlists: List[Dict[str, Any]], all_csv_records: List[Dict[str, Any]], ignored_artists: List[str] = None):
        self.matched_setlists = matched_setlists
        self.all_csv_records = all_csv_records
        self.ignored_artists = [a.lower().strip() for a in (ignored_artists or IGNORED_ARTISTS)]

        # Build canonical artist casing map (priority: official casing from setlists > CSV casing)
        self.canonical_artist_map = {}
        for pair in self.matched_setlists:
            sl = pair.get("setlist")
            if isinstance(sl, dict):
                name = sl.get("artist", {}).get("name") if isinstance(sl.get("artist"), dict) else sl.get("artist")
                if name and isinstance(name, str) and name.strip():
                    self._register_canonical(name.strip())
            if pair.get("artist") and isinstance(pair.get("artist"), str) and pair.get("artist").strip():
                self._register_canonical(pair["artist"].strip())

        for rec in self.all_csv_records:
            for art in rec.get("artists", []):
                if art and isinstance(art, str) and art.strip():
                    self._register_canonical(art.strip())
            if rec.get("primary_artist") and isinstance(rec.get("primary_artist"), str) and rec["primary_artist"].strip():
                self._register_canonical(rec["primary_artist"].strip())

    def _register_canonical(self, name: str):
        key = name.strip().lower()
        if not key:
            return
        if key not in self.canonical_artist_map:
            self.canonical_artist_map[key] = name.strip()
        else:
            existing = self.canonical_artist_map[key]
            # Prefer title/mixed case over ALL UPPERCASE or ALL lowercase
            if (existing.isupper() or existing.islower()) and (not name.isupper() and not name.islower()):
                self.canonical_artist_map[key] = name.strip()
            elif existing.isupper() and not name.isupper():
                self.canonical_artist_map[key] = name.strip()

    def _canonical_name(self, artist_name: str) -> str:
        if not artist_name:
            return ""
        return self.canonical_artist_map.get(artist_name.strip().lower(), artist_name.strip())

    def _is_ignored(self, artist_name: str) -> bool:
        if not artist_name:
            return False
        norm = artist_name.lower().strip()
        return any(ign == norm or ign in norm for ign in self.ignored_artists)

    def compute_all_metrics(self) -> Dict[str, Any]:
        song_counter = Counter()
        artist_counter = Counter()
        venue_counter = Counter()
        artist_song_map = defaultdict(lambda: Counter())
        artist_song_occurrences = defaultdict(lambda: defaultdict(list))
        yearly_concerts = Counter()
        yearly_songs = Counter()
        
        all_songs_list = []
        artist_setlists = defaultdict(list)
        all_artists_seen = set()

        effective_tenures = get_effective_band_tenures() or {}

        occurred_records = [r for r in self.all_csv_records if record_has_occurred(r)]

        for rec in occurred_records:
            if rec.get("year"):
                yearly_concerts[rec["year"]] += 1
            if rec.get("venue"):
                venue_counter[rec["venue"]] += 1
            for raw_art in rec.get("artists", []):
                art = self._canonical_name(raw_art)
                if not self._is_ignored(art):
                    artist_counter[art] += 1
                    all_artists_seen.add(art)

        first_heard_tracker = {}
        covers_count = 0
        total_songs_played = 0

        sorted_pairs = sorted(
            [p for p in self.matched_setlists if p.get("setlist") and record_has_occurred(p.get("csv", {}))],
            key=lambda p: str(p["csv"].get("date_obj") or "")
        )

        for pair in sorted_pairs:
            csv_rec = pair["csv"]
            sl = pair["setlist"]
            raw_sl_artist = sl.get("artist", {}).get("name", csv_rec["primary_artist"])
            sl_artist = self._canonical_name(raw_sl_artist)
            if self._is_ignored(sl_artist):
                continue

            date_str = csv_rec.get("display_date", csv_rec.get("raw_date", ""))
            year = csv_rec.get("year")
            venue = csv_rec.get("venue", "")
            setlist_url = sl.get("url", "")

            # Identify active musicians for this artist at the time of the concert
            art_tenures = effective_tenures.get(sl_artist.lower().strip(), [])
            active_musicians = []
            for mem in art_tenures:
                m_start = mem.get("start", 1900)
                m_end = mem.get("end")
                if year:
                    if year < m_start:
                        continue
                    if m_end is not None and year > m_end:
                        continue
                active_musicians.append(mem["musician"])

            sets = sl.get("sets", {}).get("set", [])
            concert_song_names = []

            # Flatten setlist tracks to compute relative set positions and slots
            flat_songs = []
            has_encore = any(bool(s.get("encore")) for s in sets)
            for s_idx, s in enumerate(sets):
                is_encore = bool(s.get("encore"))
                encore_num = s.get("encore")
                set_name = s.get("name", "")
                song_list = s.get("song", [])
                for s_order, song_obj in enumerate(song_list):
                    if song_obj.get("tape"):
                        continue
                    name = song_obj.get("name", "").strip()
                    if name:
                        flat_songs.append({
                            "song_obj": song_obj,
                            "name": name,
                            "is_encore": is_encore,
                            "encore_num": encore_num,
                            "set_name": set_name,
                            "set_idx": s_idx,
                            "is_last_in_set": (s_order == len(song_list) - 1)
                        })

            total_set_tracks = len(flat_songs)
            main_sets = [s for s in sets if not s.get("encore")]

            for idx, item_data in enumerate(flat_songs):
                name = item_data["name"]
                song_obj = item_data["song_obj"]
                track_num = idx + 1
                pct = round((track_num / total_set_tracks) * 100) if total_set_tracks > 0 else 100
                set_name = item_data["set_name"]
                set_idx = item_data["set_idx"]

                # Determine slot label and category
                if track_num == 1:
                    slot = "Opener"
                    slot_category = "opener"
                elif item_data["is_encore"]:
                    if track_num == total_set_tracks:
                        slot = "Show Closer"
                    else:
                        slot = f"Encore {item_data['encore_num']}" if item_data["encore_num"] else "Encore"
                    slot_category = "encore"
                elif track_num == total_set_tracks:
                    slot = "Show Closer"
                    slot_category = "closer"
                elif item_data["is_last_in_set"]:
                    if len(main_sets) > 1 and set_idx < len(main_sets) - 1:
                        slot = f"{set_name} Closer" if set_name else f"Set {set_idx + 1} Closer"
                        slot_category = "closer"
                    elif has_encore:
                        slot = "Main Set Closer"
                        slot_category = "closer"
                    elif pct <= 35:
                        slot = "Early Set"
                        slot_category = "early"
                    elif pct <= 70:
                        slot = "Mid-Set"
                        slot_category = "mid"
                    else:
                        slot = "Late Set"
                        slot_category = "late"
                elif pct <= 35:
                    slot = "Early Set"
                    slot_category = "early"
                elif pct <= 70:
                    slot = "Mid-Set"
                    slot_category = "mid"
                else:
                    slot = "Late Set"
                    slot_category = "late"

                position_display = f"{slot} ({pct}% • #{track_num}/{total_set_tracks})"

                total_songs_played += 1
                song_counter[(sl_artist, name)] += 1
                artist_song_map[sl_artist][name] += 1
                concert_song_names.append(name)
                if year:
                    yearly_songs[year] += 1

                is_cover = bool(song_obj.get("cover"))
                if is_cover:
                    covers_count += 1

                with_guest = song_obj.get("with", {}).get("name") if isinstance(song_obj.get("with"), dict) else (song_obj.get("with") or None)
                info_str = song_obj.get("info", "")

                occ_info = {
                    "date": date_str,
                    "venue": venue,
                    "year": year,
                    "is_cover": is_cover,
                    "cover_original": song_obj.get("cover", {}).get("name") if is_cover else None,
                    "with_guest": with_guest,
                    "info": info_str,
                    "setlist_url": setlist_url,
                    "track_num": track_num,
                    "total_tracks": total_set_tracks,
                    "pct": pct,
                    "slot": slot,
                    "slot_category": slot_category,
                    "position_display": position_display,
                    "musicians": active_musicians
                }
                artist_song_occurrences[sl_artist][name].append(occ_info)

                if (sl_artist, name) not in first_heard_tracker:
                    first_heard_tracker[(sl_artist, name)] = {
                        "date": date_str,
                        "venue": venue,
                        "year": year
                    }

                all_songs_list.append({
                    "artist": sl_artist,
                    "song": name,
                    "date": date_str,
                    "year": year,
                    "venue": venue,
                    "is_cover": is_cover,
                    "cover_original": song_obj.get("cover", {}).get("name") if is_cover else None,
                    "with_guest": with_guest,
                    "info": info_str,
                    "setlist_url": setlist_url,
                    "track_num": track_num,
                    "total_tracks": total_set_tracks,
                    "pct": pct,
                    "slot": slot,
                    "slot_category": slot_category,
                    "position_display": position_display,
                    "musicians": active_musicians
                })

            if concert_song_names:
                artist_setlists[sl_artist].append({
                    "date": date_str,
                    "venue": venue,
                    "songs": concert_song_names
                })

        top_songs = []
        for (artist, song), count in song_counter.most_common(50):
            first_seen = first_heard_tracker.get((artist, song), {})
            top_songs.append({
                "song": song,
                "artist": artist,
                "count": count,
                "first_seen_date": first_seen.get("date", "-"),
                "first_seen_venue": first_seen.get("venue", "-")
            })

        top_artists = []
        for artist, count in artist_counter.most_common(50):
            unique_songs = len(artist_song_map.get(artist, {}))
            total_songs_by_art = sum(artist_song_map.get(artist, {}).values())
            top_artists.append({
                "artist": artist,
                "concert_count": count,
                "unique_songs_heard": unique_songs,
                "total_songs_heard": total_songs_by_art
            })

        top_venues = [{"venue": v, "count": c} for v, c in venue_counter.most_common(25)]

        # Prepare structured artist drill-down data sorted by highest songs heard
        artist_drilldown_raw = defaultdict(lambda: {"songs_dict": defaultdict(lambda: {"count": 0, "occurrences": []})})
        for artist, songs_dict in artist_song_map.items():
            can_artist = self._canonical_name(artist)
            for song_name, play_count in songs_dict.items():
                occs = artist_song_occurrences[artist][song_name]
                s_entry = artist_drilldown_raw[can_artist]["songs_dict"][song_name]
                s_entry["count"] += play_count
                s_entry["occurrences"].extend(occs)

        artist_drilldown = {}
        for can_artist, raw_data in artist_drilldown_raw.items():
            songs_dict = raw_data["songs_dict"]
            sorted_songs = []
            for song_name, s_info in sorted(songs_dict.items(), key=lambda x: (-x[1]["count"], x[0])):
                occs = s_info["occurrences"]
                sorted_songs.append({
                    "song": song_name,
                    "count": s_info["count"],
                    "first_heard": occs[0]["date"] if occs else "-",
                    "occurrences": occs
                })
            artist_drilldown[can_artist] = {
                "artist": can_artist,
                "total_plays": sum(s["count"] for s in sorted_songs),
                "unique_songs": len(sorted_songs),
                "songs": sorted_songs
            }

        # Ensure all seen artists are included in artist_drilldown even if no setlists are matched yet
        for artist, concert_count in artist_counter.items():
            can_artist = self._canonical_name(artist)
            if can_artist not in artist_drilldown:
                artist_drilldown[can_artist] = {
                    "artist": can_artist,
                    "concert_count": concert_count,
                    "total_plays": 0,
                    "unique_songs": 0,
                    "songs": []
                }
            else:
                artist_drilldown[can_artist]["concert_count"] = concert_count

        # Sort artist_drilldown by highest total plays heard, then unique songs, then concert count
        artist_drilldown = dict(
            sorted(
                artist_drilldown.items(),
                key=lambda x: (x[1]["total_plays"], x[1]["unique_songs"], x[1].get("concert_count", 0)),
                reverse=True
            )
        )

        setlist_variation = []
        for artist, sl_list in artist_setlists.items():
            if len(sl_list) >= 2:
                overlaps = []
                for i in range(len(sl_list)):
                    for j in range(i + 1, len(sl_list)):
                        set_a = set(sl_list[i]["songs"])
                        set_b = set(sl_list[j]["songs"])
                        if set_a or set_b:
                            jaccard = len(set_a & set_b) / len(set_a | set_b)
                            overlaps.append(jaccard)
                avg_overlap = (sum(overlaps) / len(overlaps)) * 100 if overlaps else 0
                setlist_variation.append({
                    "artist": artist,
                    "shows_analyzed": len(sl_list),
                    "unique_songs_total": len(artist_song_map[artist]),
                    "avg_setlist_overlap_pct": round(avg_overlap, 1),
                    "freshness_pct": round(100.0 - avg_overlap, 1)
                })
        setlist_variation.sort(key=lambda x: x["shows_analyzed"], reverse=True)

        top_year = None
        if yearly_concerts:
            best_yr, best_cnt = max(yearly_concerts.items(), key=lambda x: (x[1], x[0]))
            top_year = {"year": best_yr, "count": best_cnt}

        return {
            "total_concerts": len(occurred_records),
            "total_unique_artists": len(all_artists_seen),
            "total_songs_heard": total_songs_played,
            "unique_songs_heard": len(song_counter),
            "total_venues": len(venue_counter),
            "covers_count": covers_count,
            "top_songs": top_songs,
            "top_artists": top_artists,
            "top_venues": top_venues,
            "top_year": top_year,
            "yearly_concerts": dict(sorted(yearly_concerts.items())),
            "yearly_songs": dict(sorted(yearly_songs.items())),
            "artist_song_map": {k: dict(v) for k, v in artist_song_map.items()},
            "artist_drilldown": artist_drilldown,
            "setlist_variation": setlist_variation,
            "all_songs_list": all_songs_list
        }

    def compute_concert_drilldown(self, album_enrichments: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Builds a comprehensive chronological drilldown of all concert events (latest to oldest),
        grouped by event and artist, with set 1/2/encore groupings, nth-time-seen for artists,
        nth-time-heard for tracks, percentage of artist shows seen where track was played,
        and song age at time of concert.
        """
        if album_enrichments is None:
            album_enrichments = {}

        # Map matched setlists by (csv_id, artist_name)
        matched_map = {}
        for p in self.matched_setlists:
            if p.get("csv") and p.get("artist") and p.get("setlist"):
                c_id = p["csv"]["id"]
                art_norm = p["artist"].strip().lower()
                matched_map[(c_id, art_norm)] = p["setlist"]

        # Sort CSV records chronologically (oldest to newest) to build accurate cumulative counts
        sorted_records = sorted(
            self.all_csv_records,
            key=lambda r: (r.get("date_obj") is not None, str(r.get("date_obj") or ""))
        )

        artist_show_counter = defaultdict(int)
        artist_song_counter = defaultdict(lambda: defaultdict(int))
        concerts_drilldown = []
        effective_tenures = get_effective_band_tenures() or {}

        for rec in sorted_records:
            c_id = rec["id"]
            is_occurred = record_has_occurred(rec)
            date_str = rec.get("display_date", rec.get("raw_date", ""))
            venue = rec.get("venue", "")
            year = rec.get("year")
            artists = [a for a in rec.get("artists", []) if not self._is_ignored(a)]
            
            artists_data = []
            total_songs_in_event = 0

            for raw_art in artists:
                can_art = self._canonical_name(raw_art)
                if is_occurred:
                    artist_show_counter[can_art] += 1
                    artist_seen_nth = artist_show_counter[can_art]
                else:
                    artist_seen_nth = artist_show_counter[can_art]

                # Identify active musicians for this artist at the time of the concert
                art_tenures = effective_tenures.get(can_art.lower().strip(), [])
                active_musicians = []
                for mem in art_tenures:
                    m_start = mem.get("start", 1900)
                    m_end = mem.get("end")
                    if year:
                        if year < m_start:
                            continue
                        if m_end is not None and year > m_end:
                            continue
                    active_musicians.append({
                        "musician": mem["musician"],
                        "role": mem.get("role", "Musician"),
                        "instrument": mem.get("instrument", "Other"),
                        "start": m_start,
                        "end": m_end,
                    })

                sl = matched_map.get((c_id, can_art.strip().lower()))
                grouped_sets = []
                setlist_url = None
                artist_songs_played = 0

                if sl:
                    setlist_url = sl.get("url", "")
                    sets = sl.get("sets", {}).get("set", [])
                    set_counter = 0
                    
                    for s in sets:
                        is_encore = bool(s.get("encore"))
                        encore_num = s.get("encore")
                        custom_name = s.get("name", "").strip()
                        
                        if is_encore:
                            set_label = f"Encore {encore_num}" if encore_num else "Encore"
                        elif custom_name:
                            set_label = custom_name
                        else:
                            set_counter += 1
                            set_label = f"Set {set_counter}" if len([st for st in sets if not st.get('encore')]) > 1 else "Main Set"
                        
                        songs_in_set = []
                        for s_song in s.get("song", []):
                            if s_song.get("tape"):
                                continue
                            s_name = s_song.get("name", "").strip()
                            if not s_name:
                                continue
                            
                            artist_song_counter[can_art][s_name] += 1
                            song_heard_nth = artist_song_counter[can_art][s_name]
                            pct_shows = round((song_heard_nth / artist_seen_nth) * 100)
                            
                            # Album and release year info
                            k = f"{can_art}_{s_name}".lower()
                            enrich_info = album_enrichments.get(k, {})
                            album = enrich_info.get("album", "Non-Album / Singles")
                            rel_year = enrich_info.get("release_year")
                            is_cov = bool(s_song.get("cover")) or enrich_info.get("is_cover")
                            cov_artist = s_song.get("cover", {}).get("name") if isinstance(s_song.get("cover"), dict) else (enrich_info.get("original_artist") or None)
                            if is_cov and album == "Non-Album / Singles":
                                album = "Covers"
                            
                            song_age_str = None
                            if year and rel_year:
                                diff = year - rel_year
                                if diff < 0:
                                    song_age_str = f"Unreleased / Debut (rel. {rel_year})"
                                elif diff == 0:
                                    song_age_str = f"Released same year ({rel_year})"
                                elif diff == 1:
                                    song_age_str = f"1 year old ({rel_year})"
                                else:
                                    song_age_str = f"{diff} years old ({rel_year})"
                            elif rel_year:
                                song_age_str = f"Released {rel_year}"

                            is_cover = bool(s_song.get("cover")) or bool(is_cov)
                            cover_orig = cov_artist
                            with_guest = s_song.get("with", {}).get("name") if isinstance(s_song.get("with"), dict) else (s_song.get("with") or None)
                            info_str = s_song.get("info", "")

                            songs_in_set.append({
                                "song": s_name,
                                "song_heard_nth": song_heard_nth,
                                "artist_seen_nth": artist_seen_nth,
                                "pct_shows": pct_shows,
                                "album": album,
                                "release_year": rel_year,
                                "album_display": f"{album} ({rel_year})" if rel_year else album,
                                "song_age_str": song_age_str,
                                "is_cover": is_cover,
                                "cover_original": cover_orig,
                                "with_guest": with_guest,
                                "info": info_str,
                                "tape": bool(s_song.get("tape"))
                            })
                            artist_songs_played += 1
                            total_songs_in_event += 1

                        if songs_in_set:
                            grouped_sets.append({
                                "set_label": set_label,
                                "is_encore": is_encore,
                                "songs": songs_in_set
                            })

                # Build reconstructed setlist text for easy pre-filling in edit modal
                setlist_lines = []
                for s_group in grouped_sets:
                    if s_group.get("is_encore"):
                        setlist_lines.append("Encore:")
                    elif s_group.get("set_label") and s_group.get("set_label") not in ("Main Set", "Set 1"):
                        setlist_lines.append(f"{s_group.get('set_label')}:")
                    for trk in s_group.get("songs", []):
                        s_line = trk.get("song", "")
                        if trk.get("is_cover") and trk.get("cover_original"):
                            s_line += f" ({trk.get('cover_original')} cover)"
                        elif trk.get("is_cover"):
                            s_line += " (Cover)"
                        if trk.get("info"):
                            s_line += f" ({trk.get('info')})"
                        if s_line:
                            setlist_lines.append(s_line)
                artist_setlist_text = "\n".join(setlist_lines)
                setlistfm_id = ""
                setify_url = ""
                if sl and sl.get("id"):
                    setlistfm_id = str(sl.get("id")).strip()
                elif setlist_url:
                    m = re.search(r'([a-zA-Z0-9]+)\.html', setlist_url)
                    if m:
                        setlistfm_id = m.group(1)
                art_favs = rec.get("artist_favorites", {})
                art_ca_ids = rec.get("artist_ca_ids", {})
                art_k = can_art.lower().strip()
                is_art_fav = bool(art_favs.get(art_k, False))
                ca_id = art_ca_ids.get(art_k)

                artists_data.append({
                    "artist": can_art,
                    "ca_id": ca_id,
                    "is_favorite": is_art_fav,
                    "artist_seen_nth": artist_seen_nth,
                    "has_setlist": bool(sl and grouped_sets),
                    "setlist_url": setlist_url,
                    "setlistfm_id": setlistfm_id,
                    "setify_url": setify_url,
                    "total_songs": artist_songs_played,
                    "grouped_sets": grouped_sets,
                    "setlist_text": artist_setlist_text,
                    "musicians": active_musicians
                })

            primary_art = rec.get("primary_artist") or (artists[0] if artists else "")
            supporting_arts = ", ".join(artists[1:]) if len(artists) > 1 else ""
            primary_setlist_text = artists_data[0].get("setlist_text", "") if artists_data else ""
            is_offline = bool(rec.get("is_custom_offline", False))
            is_synced = (
                not is_offline
                and (
                    rec.get("source") == "setlistfm"
                    or any(bool(a.get("setlist_url")) for a in artists_data)
                    or bool(rec.get("has_setlistfm_id", False))
                )
            )
            has_fav_artist = any(bool(a.get("is_favorite")) for a in artists_data)

            concerts_drilldown.append({
                "id": c_id,
                "db_id": rec.get("db_id"),
                "date": date_str,
                "raw_date": rec.get("raw_date", date_str),
                "venue": venue,
                "city": rec.get("city", ""),
                "state": rec.get("state", ""),
                "country": rec.get("country", "United States"),
                "year": year,
                "primary_artist": primary_art,
                "supporting_artists": supporting_arts,
                "raw_artists": rec.get("raw_artists", ", ".join(artists)),
                "artists": artists_data,
                "notes": rec.get("notes", ""),
                "source": rec.get("source", "manual"),
                "is_custom_offline": is_offline,
                "is_setlistfm_synced": is_synced,
                "setlist_text": primary_setlist_text,
                "total_artists": len(artists),
                "total_songs": total_songs_in_event,
                "has_any_setlist": any(a["has_setlist"] for a in artists_data),
                "is_favorite": bool(rec.get("is_favorite", False)),
                "has_favorite_artist": has_fav_artist
            })

        # Return in reverse chronological order (Latest to Oldest)
        concerts_drilldown.reverse()
        return concerts_drilldown
