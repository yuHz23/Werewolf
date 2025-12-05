from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
import random
import string

app = FastAPI()

# CORS cho frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================== MODELS ==================


class Player(BaseModel):
    id: str
    name: str
    role: Optional[str] = None       # werewolf, seer, witch, guard, gambler, prince, mage, villager
    alive: bool = True
    muted_today: bool = False
    prince_revealed: bool = False    # Prince đã lộ chưa


class Action(BaseModel):
    player_id: str
    action_type: str                 # mage_mute, guard_protect, wolf_kill, gambler_bet, gambler_skip, ...
    target_name: Optional[str] = None
    # Thêm thông tin phase / night / day để phân biệt từng đêm / ngày
    phase: Optional[str] = None      # "night" | "day"
    night_number: Optional[int] = None
    day_number: Optional[int] = None


class Room(BaseModel):
    code: str
    host_secret: str
    players: Dict[str, Player] = Field(default_factory=dict)

    phase: str = "lobby"             # lobby | night | day | ended
    night_number: int = 0
    day_number: int = 0
    started: bool = False

    actions: List[Action] = Field(default_factory=list)

    # Phù thủy / Bảo vệ
    witch_has_heal: bool = True
    witch_has_poison: bool = True
    last_guard_target_name: Optional[str] = None

    deaths_last_night: List[str] = Field(default_factory=list)
    muted_for_today: List[str] = Field(default_factory=list)

    active_call: Optional[str] = None       # "mage", "guard", "werewolf", "seer", "witch", "gambler" | None

    voting_status: str = "idle"             # "idle" | "voting"
    vote_duration_sec: Optional[int] = None

    winner: Optional[str] = None            # "village" | "werewolves" | None


rooms: Dict[str, Room] = {}


class CreateRoomResp(BaseModel):
    room_code: str
    host_secret: str


class JoinReq(BaseModel):
    name: str


class JoinResp(BaseModel):
    room_code: str
    player_id: str


class PlayerPublic(BaseModel):
    name: str
    alive: bool
    muted_today: bool


class PlayerStateResp(BaseModel):
    room_code: str
    player: Player
    players: List[PlayerPublic]
    phase: str
    night_number: int
    day_number: int
    deaths_last_night: List[str]
    active_call: Optional[str]
    wolf_mates: List[str]
    winner: Optional[str]


class HostStartReq(BaseModel):
    host_secret: str


class HostPhaseReq(BaseModel):
    host_secret: str
    phase: str


class HostResolveReq(BaseModel):
    host_secret: str


class ActionReq(BaseModel):
    player_id: str
    action_type: str
    target_name: Optional[str] = None


class SeerResultResp(BaseModel):
    target_name: str
    is_werewolf: bool


class VillageStateResp(BaseModel):
    room_code: str
    phase: str
    night_number: int
    day_number: int
    players: List[PlayerPublic]
    winner: Optional[str]


class HostPlayerPublic(BaseModel):
    name: str
    alive: bool
    role: Optional[str]
    muted_today: bool
    prince_revealed: bool


class HostStateResp(BaseModel):
    room_code: str
    phase: str
    night_number: int
    day_number: int
    players: List[HostPlayerPublic]
    deaths_last_night: List[str]
    witch_has_heal: bool
    witch_has_poison: bool
    active_call: Optional[str]
    voting_status: str
    vote_duration_sec: Optional[int]
    winner: Optional[str]


class HostCallReq(BaseModel):
    host_secret: str
    role: Optional[str]


class WitchInfoResp(BaseModel):
    victim_name: Optional[str]
    can_heal: bool
    can_poison: bool


class StartVotingReq(BaseModel):
    host_secret: str
    duration_sec: Optional[int] = None


class VotePreviewResp(BaseModel):
    candidate_name: Optional[str]
    votes: Dict[str, int]


class RoleProgressResp(BaseModel):
    pending: List[str]
    done: bool


# ================== UTILS ==================


def gen_code(length: int = 4) -> str:
    return "".join(random.choice(string.digits) for _ in range(length))


def gen_id(length: int = 8) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def get_room_or_404(code: str) -> Room:
    room = rooms.get(code)
    if not room:
        raise HTTPException(404, "Room không tồn tại")
    return room


def find_player_by_name(room: Room, name: str) -> Optional[Player]:
    for p in room.players.values():
        if p.name == name:
            return p
    return None


def compute_winner(room: Room) -> Optional[str]:
    alive_wolves = sum(1 for p in room.players.values() if p.alive and p.role == "werewolf")
    alive_others = sum(1 for p in room.players.values() if p.alive and p.role != "werewolf")

    if alive_wolves == 0 and alive_others > 0:
        return "village"
    if alive_wolves > 0 and alive_wolves >= alive_others:
        return "werewolves"
    return None


def compute_lynch_votes(room: Room):
    votes_actions = [a for a in room.actions if a.action_type == "vote_lynch" and a.target_name]
    last_vote_by_player: Dict[str, str] = {}
    for a in votes_actions:
        last_vote_by_player[a.player_id] = a.target_name

    counter: Dict[str, int] = {}
    for target in last_vote_by_player.values():
        counter[target] = counter.get(target, 0) + 1

    if not counter:
        return None, {}
    candidate_name = max(counter.items(), key=lambda x: x[1])[0]
    return candidate_name, counter


# ================== HOST ENDPOINTS ==================


@app.post("/api/rooms", response_model=CreateRoomResp)
def create_room():
    code = gen_code()
    while code in rooms:
        code = gen_code()
    secret = gen_id()
    room = Room(code=code, host_secret=secret)
    rooms[code] = room
    return CreateRoomResp(room_code=code, host_secret=secret)


@app.get("/api/rooms/{room_code}/host_state", response_model=HostStateResp)
def host_state(room_code: str, host_secret: str = Query(...)):
    room = get_room_or_404(room_code)
    if room.host_secret != host_secret:
        raise HTTPException(403, "Sai host_secret")

    players = [
        HostPlayerPublic(
            name=p.name,
            alive=p.alive,
            role=p.role,
            muted_today=p.muted_today,
            prince_revealed=p.prince_revealed,
        )
        for p in room.players.values()
    ]

    return HostStateResp(
        room_code=room.code,
        phase=room.phase,
        night_number=room.night_number,
        day_number=room.day_number,
        players=players,
        deaths_last_night=room.deaths_last_night,
        witch_has_heal=room.witch_has_heal,
        witch_has_poison=room.witch_has_poison,
        active_call=room.active_call,
        voting_status=room.voting_status,
        vote_duration_sec=room.vote_duration_sec,
        winner=room.winner,
    )


@app.post("/api/rooms/{room_code}/start")
def start_game(room_code: str, body: HostStartReq):
    room = get_room_or_404(room_code)
    if room.host_secret != body.host_secret:
        raise HTTPException(403, "Sai host_secret")

    n = len(room.players)
    if n < 4:
        raise HTTPException(400, "Cần ít nhất 4 người chơi.")

    room.winner = None

    num_wolves = 1 if n < 5 else 2
    base_roles = (
        ["werewolf"] * num_wolves
        + ["seer", "witch", "guard", "gambler", "prince", "mage"]
    )

    player_ids = list(room.players.keys())
    random.shuffle(player_ids)

    if n <= len(base_roles):
        assigned_roles = base_roles[:n]
    else:
        assigned_roles = base_roles + ["villager"] * (n - len(base_roles))

    for pid, role in zip(player_ids, assigned_roles):
        room.players[pid].role = role
        room.players[pid].alive = True
        room.players[pid].muted_today = False
        room.players[pid].prince_revealed = False

    room.started = True
    room.phase = "night"
    room.night_number = 1
    room.day_number = 0
    room.actions.clear()
    room.deaths_last_night.clear()
    room.muted_for_today.clear()
    room.active_call = None
    room.witch_has_heal = True
    room.witch_has_poison = True
    room.voting_status = "idle"
    room.vote_duration_sec = None

    return {"ok": True, "message": "Game đã bắt đầu, đang là ĐÊM 1."}


@app.post("/api/rooms/{room_code}/phase")
def host_set_phase(room_code: str, body: HostPhaseReq):
    room = get_room_or_404(room_code)
    if room.host_secret != body.host_secret:
        raise HTTPException(403, "Sai host_secret")
    if room.winner is not None:
        raise HTTPException(400, "Game đã kết thúc.")
    if body.phase not in ("night", "day", "lobby"):
        raise HTTPException(400, "Phase không hợp lệ")

    room.phase = body.phase

    if body.phase == "night":
        room.night_number += 1
        room.actions.clear()
        room.deaths_last_night.clear()
        room.muted_for_today.clear()
        room.active_call = None
        room.voting_status = "idle"
        room.vote_duration_sec = None
        for p in room.players.values():
            p.muted_today = False
    elif body.phase == "day":
        room.day_number += 1
        room.active_call = None
        room.voting_status = "idle"
        room.vote_duration_sec = None

    return {
      "ok": True,
      "phase": room.phase,
      "night_number": room.night_number,
      "day_number": room.day_number,
    }


@app.post("/api/rooms/{room_code}/call_role")
def host_call_role(room_code: str, body: HostCallReq):
    room = get_room_or_404(room_code)
    if room.host_secret != body.host_secret:
        raise HTTPException(403, "Sai host_secret")
    if room.winner is not None:
        raise HTTPException(400, "Game đã kết thúc.")

    room.active_call = body.role
    return {"ok": True, "active_call": room.active_call}


@app.post("/api/rooms/{room_code}/resolve_night")
def host_resolve_night(room_code: str, body: HostResolveReq):
    room = get_room_or_404(room_code)
    if room.host_secret != body.host_secret:
        raise HTTPException(403, "Sai host_secret")
    if room.phase != "night":
        raise HTTPException(400, "Chỉ resolve_night khi đang là ĐÊM")
    if room.winner is not None:
        raise HTTPException(400, "Game đã kết thúc.")

    room.deaths_last_night.clear()
    room.muted_for_today.clear()
    for p in room.players.values():
        p.muted_today = False

    # Lấy action của ĐÊM hiện tại
    night_actions = [
        a for a in room.actions
        if a.phase == "night" and a.night_number == room.night_number
    ]

    mage_actions = [a for a in night_actions if a.action_type == "mage_mute"]
    guard_actions = [a for a in night_actions if a.action_type == "guard_protect"]
    wolf_actions = [a for a in night_actions if a.action_type == "wolf_kill"]
    gambler_actions = [a for a in night_actions if a.action_type == "gambler_bet"]

    witch_heal_decisions = [
        a for a in night_actions if a.action_type in ("witch_heal", "witch_no_heal")
    ]
    witch_poison_decisions = [
        a for a in night_actions if a.action_type in ("witch_poison", "witch_no_poison")
    ]

    # 1. Mage mute
    if mage_actions:
        last_mage = mage_actions[-1]
        if last_mage.target_name:
            room.muted_for_today.append(last_mage.target_name)
            target = find_player_by_name(room, last_mage.target_name)
            if target:
                target.muted_today = True

    # 2. Guard
    guard_target_name = None
    if guard_actions:
        last_guard = guard_actions[-1]
        guard_target_name = last_guard.target_name
        room.last_guard_target_name = guard_target_name

    # 3. Wolves victim
    wolf_target_name = None
    if wolf_actions:
        counter = {}
        for a in wolf_actions:
            if not a.target_name:
                continue
            counter[a.target_name] = counter.get(a.target_name, 0) + 1
        if counter:
            wolf_target_name = max(counter.items(), key=lambda x: x[1])[0]

    # 4. Gambler
    gambler_target_name = None
    if room.night_number >= 2 and gambler_actions:
        last_gambler = gambler_actions[-1]
        gambler_target_name = last_gambler.target_name

    # 5. Witch heal + poison
    use_heal = False
    if room.witch_has_heal and witch_heal_decisions:
        last_heal = witch_heal_decisions[-1]
        if last_heal.action_type == "witch_heal":
            use_heal = True

    poison_target_name = None
    if room.witch_has_poison and witch_poison_decisions:
        last_poison = witch_poison_decisions[-1]
        if last_poison.action_type == "witch_poison":
            poison_target_name = last_poison.target_name

    deaths: List[str] = []

    # Sói cắn
    wolf_victim = wolf_target_name
    if wolf_victim and guard_target_name and wolf_victim == guard_target_name:
        wolf_victim = None
    if wolf_victim and use_heal:
        wolf_victim = None
        room.witch_has_heal = False
    if wolf_victim:
        deaths.append(wolf_victim)

    # Con bạc
    if gambler_target_name:
        deaths.append(gambler_target_name)

    # Thuốc độc
    if poison_target_name:
        deaths.append(poison_target_name)
        room.witch_has_poison = False

    deaths_unique: List[str] = []
    for name in deaths:
        if name not in deaths_unique:
            deaths_unique.append(name)

    for name in deaths_unique:
        p = find_player_by_name(room, name)
        if p and p.alive:
            p.alive = False

    room.deaths_last_night = deaths_unique
    room.active_call = None

    winner = compute_winner(room)
    if winner:
        room.winner = winner
        room.phase = "ended"
        room.voting_status = "idle"
        room.vote_duration_sec = None

    return {
        "ok": True,
        "deaths": deaths_unique,
        "muted_for_today": room.muted_for_today,
        "winner": winner,
    }


@app.post("/api/rooms/{room_code}/start_voting")
def start_voting(room_code: str, body: StartVotingReq):
    room = get_room_or_404(room_code)
    if room.host_secret != body.host_secret:
        raise HTTPException(403, "Sai host_secret")
    if room.phase != "day":
        raise HTTPException(400, "Chỉ bắt đầu vote khi đang là NGÀY")
    if room.winner is not None:
        raise HTTPException(400, "Game đã kết thúc.")

    room.voting_status = "voting"
    room.vote_duration_sec = body.duration_sec
    return {
        "ok": True,
        "voting_status": room.voting_status,
        "vote_duration_sec": room.vote_duration_sec,
    }


@app.post("/api/rooms/{room_code}/vote_preview", response_model=VotePreviewResp)
def vote_preview(room_code: str, body: HostResolveReq):
    room = get_room_or_404(room_code)
    if room.host_secret != body.host_secret:
        raise HTTPException(403, "Sai host_secret")
    candidate, counter = compute_lynch_votes(room)
    return VotePreviewResp(candidate_name=candidate, votes=counter)


@app.post("/api/rooms/{room_code}/resolve_day")
def host_resolve_day(room_code: str, body: HostResolveReq):
    room = get_room_or_404(room_code)
    if room.host_secret != body.host_secret:
        raise HTTPException(403, "Sai host_secret")
    if room.phase != "day":
        raise HTTPException(400, "Chỉ resolve_day khi đang là NGÀY")
    if room.winner is not None:
        raise HTTPException(400, "Game đã kết thúc.")

    candidate_name, counter = compute_lynch_votes(room)
    if not candidate_name:
        room.voting_status = "idle"
        room.vote_duration_sec = None
        winner_now = compute_winner(room)
        return {
            "ok": True,
            "lynched": None,
            "prince_revealed": False,
            "message": "Không ai bị vote.",
            "winner": winner_now,
        }

    target = find_player_by_name(room, candidate_name)
    if not target:
        room.voting_status = "idle"
        room.vote_duration_sec = None
        winner_now = compute_winner(room)
        return {
            "ok": True,
            "lynched": None,
            "prince_revealed": False,
            "message": "Không tìm thấy người bị vote.",
            "winner": winner_now,
        }

    # Prince: lần đầu lộ role, không chết
    if target.role == "prince" and not target.prince_revealed:
        target.prince_revealed = True
        winner_now = compute_winner(room)
        room.active_call = None
        room.voting_status = "idle"
        room.vote_duration_sec = None
        if winner_now:
            room.winner = winner_now
            room.phase = "ended"
        return {
            "ok": True,
            "lynched": target.name,
            "prince_revealed": True,
            "message": f"{target.name} là Hoàng tử phe dân làng! Lần này không chết.",
            "winner": winner_now,
        }

    if target.alive:
        target.alive = False
    winner_now = compute_winner(room)
    room.active_call = None
    room.voting_status = "idle"
    room.vote_duration_sec = None
    if winner_now:
        room.winner = winner_now
        room.phase = "ended"

    return {
        "ok": True,
        "lynched": target.name,
        "prince_revealed": False,
        "message": f"{target.name} đã bị treo cổ.",
        "winner": winner_now,
    }


@app.get("/api/rooms/{room_code}/role_progress", response_model=RoleProgressResp)
def role_progress(
    room_code: str,
    role: str = Query(...),
    host_secret: str = Query(...),
):
    """
    AI Host dùng để check role đã xong lượt chưa (theo ĐÊM hiện tại).
    Witch: bắt buộc phải có cả heal decision (heal / no heal)
           và poison decision (poison / no poison) trong ĐÊM hiện tại.
    """
    room = get_room_or_404(room_code)
    if room.host_secret != host_secret:
        raise HTTPException(403, "Sai host_secret")

    valid_roles = {"mage", "guard", "werewolf", "seer", "witch", "gambler"}
    if role not in valid_roles:
        raise HTTPException(400, "Role không hợp lệ")

    alive_role_players = [
        p for p in room.players.values()
        if p.alive and p.role == role
    ]
    if not alive_role_players:
        return RoleProgressResp(pending=[], done=True)

    # Lọc action của ĐÊM hiện tại
    def night_actions_for_player(pid: str, allowed_types: Optional[List[str]] = None):
        acts = [
            a for a in room.actions
            if a.phase == "night" and a.night_number == room.night_number and a.player_id == pid
        ]
        if allowed_types is not None:
            acts = [a for a in acts if a.action_type in allowed_types]
        return acts

    # Witch: custom logic – 2 quyết định riêng biệt
    if role == "witch":
        pending: List[str] = []
        for p in alive_role_players:
            acts = night_actions_for_player(p.id)
            has_heal_decision = any(
                a.action_type in ("witch_heal", "witch_no_heal") for a in acts
            )
            has_poison_decision = any(
                a.action_type in ("witch_poison", "witch_no_poison") for a in acts
            )
            if not (has_heal_decision and has_poison_decision):
                pending.append(p.name)
        return RoleProgressResp(pending=pending, done=(len(pending) == 0))

    # Các role khác: 1 action là đủ cho mỗi đêm
    role_actions_map = {
        "mage": ["mage_mute"],
        "guard": ["guard_protect"],
        "werewolf": ["wolf_kill"],
        "seer": ["seer_inspect"],
        "gambler": ["gambler_bet", "gambler_skip"],
    }
    allowed_actions = role_actions_map[role]

    pending: List[str] = []
    for p in alive_role_players:
        acts = night_actions_for_player(p.id, allowed_types=allowed_actions)
        if not acts:
            pending.append(p.name)

    return RoleProgressResp(pending=pending, done=(len(pending) == 0))


# ================== PLAYER ENDPOINTS ==================


@app.post("/api/rooms/{room_code}/join", response_model=JoinResp)
def join_room(room_code: str, body: JoinReq):
    room = get_room_or_404(room_code)
    if room.started:
        raise HTTPException(400, "Game đã bắt đầu, không join thêm được.")

    pid = gen_id()
    room.players[pid] = Player(id=pid, name=body.name)
    return JoinResp(room_code=room.code, player_id=pid)


@app.get("/api/rooms/{room_code}/state/{player_id}", response_model=PlayerStateResp)
def get_player_state(room_code: str, player_id: str):
    room = get_room_or_404(room_code)
    player = room.players.get(player_id)
    if not player:
        raise HTTPException(404, "Player không thuộc phòng này")

    public_players = [
        PlayerPublic(name=p.name, alive=p.alive, muted_today=p.muted_today)
        for p in room.players.values()
    ]

    wolf_mates: List[str] = []
    if player.role == "werewolf":
        for p in room.players.values():
            if p.role == "werewolf" and p.id != player_id:
                wolf_mates.append(p.name)

    return PlayerStateResp(
        room_code=room.code,
        player=player,
        players=public_players,
        phase=room.phase,
        night_number=room.night_number,
        day_number=room.day_number,
        deaths_last_night=room.deaths_last_night,
        active_call=room.active_call,
        wolf_mates=wolf_mates,
        winner=room.winner,
    )


@app.post("/api/rooms/{room_code}/actions")
def post_action(room_code: str, body: ActionReq):
    room = get_room_or_404(room_code)
    player = room.players.get(body.player_id)
    if not player:
        raise HTTPException(404, "Player không thuộc phòng này")
    if not player.alive:
        raise HTTPException(400, "Bạn đã chết, không thể hành động")
    if room.winner is not None:
        raise HTTPException(400, "Game đã kết thúc.")

    # Ghi lại phase/night/day tại thời điểm action
    action = Action(
        player_id=body.player_id,
        action_type=body.action_type,
        target_name=body.target_name,
        phase=room.phase,
        night_number=room.night_number if room.phase == "night" else None,
        day_number=room.day_number if room.phase == "day" else None,
    )
    room.actions.append(action)
    return {"ok": True}


@app.post("/api/rooms/{room_code}/seer_result", response_model=SeerResultResp)
def seer_result(room_code: str, body: ActionReq):
    room = get_room_or_404(room_code)
    if not body.target_name:
        raise HTTPException(400, "Thiếu target_name")

    target = find_player_by_name(room, body.target_name)
    if not target:
        raise HTTPException(404, "Không tìm thấy người bị soi")

    return SeerResultResp(target_name=target.name, is_werewolf=(target.role == "werewolf"))


@app.get("/api/rooms/{room_code}/witch_info/{player_id}", response_model=WitchInfoResp)
def witch_info(room_code: str, player_id: str):
    room = get_room_or_404(room_code)
    player = room.players.get(player_id)
    if not player or player.role != "witch" or not player.alive:
        raise HTTPException(403, "Bạn không phải Phù thủy đang sống.")

    # Lấy action sói của ĐÊM hiện tại
    night_actions = [
        a for a in room.actions
        if a.phase == "night" and a.night_number == room.night_number
    ]
    wolf_actions = [a for a in night_actions if a.action_type == "wolf_kill"]

    wolf_target_name: Optional[str] = None
    if wolf_actions:
        counter: Dict[str, int] = {}
        for a in wolf_actions:
            if not a.target_name:
                continue
            counter[a.target_name] = counter.get(a.target_name, 0) + 1
        if counter:
            wolf_target_name = max(counter.items(), key=lambda x: x[1])[0]

    victim_name = wolf_target_name

    return WitchInfoResp(
        victim_name=victim_name,
        can_heal=bool(victim_name) and room.witch_has_heal,
        can_poison=room.witch_has_poison,
    )


# ================== VILLAGE STATE ==================


@app.get("/api/rooms/{room_code}/village_state", response_model=VillageStateResp)
def village_state(room_code: str):
    room = get_room_or_404(room_code)
    public_players = [
        PlayerPublic(name=p.name, alive=p.alive, muted_today=p.muted_today)
        for p in room.players.values()
    ]
    return VillageStateResp(
        room_code=room.code,
        phase=room.phase,
        night_number=room.night_number,
        day_number=room.day_number,
        players=public_players,
        winner=room.winner,
    )
