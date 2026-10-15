# Released under the MIT License. See LICENSE for details.
# https://youtu.be/wTgwZKiykQw?si=Cr0ybDYAcKCUNFN4
# https://discord.gg/ucyaesh
# https://bombsquad-community.web.app/home
# by: Mr.Smoothy

"""Elimination mini-game."""

# ba_meta require api 9
# (see https://ballistica.net/wiki/meta-tag-system)

from __future__ import annotations

from typing import TYPE_CHECKING

import babase
import bauiv1 as bui
import bascenev1 as bs
from bascenev1lib.actor.spazfactory import SpazFactory
from bascenev1lib.actor.scoreboard import Scoreboard
from bascenev1lib.gameutils import SharedObjects
if TYPE_CHECKING:
    from typing import Any, Sequence, Optional, Union
import random


class Icon(bs.Actor):
    """Creates in in-game icon on screen."""

    def __init__(self,
                 player: Player,
                 position: tuple[float, float],
                 scale: float,
                 show_lives: bool = True,
                 show_death: bool = True,
                 name_scale: float = 1.0,
                 name_maxwidth: float = 115.0,
                 flatness: float = 1.0,
                 shadow: float = 1.0):
        super().__init__()

        self._player = player
        self._show_lives = show_lives
        self._show_death = show_death
        self._name_scale = name_scale
        self._outline_tex = bs.gettexture('characterIconMask')

        icon = player.get_icon()
        self.node = bs.newnode('image',
                               delegate=self,
                               attrs={
                                   'texture': icon['texture'],
                                   'tint_texture': icon['tint_texture'],
                                   'tint_color': icon['tint_color'],
                                   'vr_depth': 400,
                                   'tint2_color': icon['tint2_color'],
                                   'mask_texture': self._outline_tex,
                                   'opacity': 1.0,
                                   'absolute_scale': True,
                                   'attach': 'bottomCenter'
                               })
        self._name_text = bs.newnode(
            'text',
            owner=self.node,
            attrs={
                'text': babase.Lstr(value=player.getname()),
                'color': babase.safecolor(player.team.color),
                'h_align': 'center',
                'v_align': 'center',
                'vr_depth': 410,
                'maxwidth': name_maxwidth,
                'shadow': shadow,
                'flatness': flatness,
                'h_attach': 'center',
                'v_attach': 'bottom'
            })
        if self._show_lives:
            self._lives_text = bs.newnode('text',
                                          owner=self.node,
                                          attrs={
                                              'text': 'x0',
                                              'color': (1, 1, 0.5),
                                              'h_align': 'left',
                                              'vr_depth': 430,
                                              'shadow': 1.0,
                                              'flatness': 1.0,
                                              'h_attach': 'center',
                                              'v_attach': 'bottom'
                                          })
        self.set_position_and_scale(position, scale)

    def set_position_and_scale(self, position: tuple[float, float],
                               scale: float) -> None:
        """(Re)position the icon."""
        assert self.node
        self.node.position = position
        self.node.scale = [70.0 * scale]
        self._name_text.position = (position[0], position[1] + scale * 52.0)
        self._name_text.scale = 1.0 * scale * self._name_scale
        if self._show_lives:
            self._lives_text.position = (position[0] + scale * 10.0,
                                         position[1] - scale * 43.0)
            self._lives_text.scale = 1.0 * scale

    def update_for_lives(self) -> None:
        """Update for the target player's current lives."""
        if self._player:
            lives = self._player.lives
        else:
            lives = 0
        if self._show_lives:
            if lives > 0:
                self._lives_text.text = 'x' + str(lives - 1)
            else:
                self._lives_text.text = ''
        if lives == 0:
            self._name_text.opacity = 0.2
            assert self.node
            self.node.color = (0.7, 0.3, 0.3)
            self.node.opacity = 0.2

    def handle_player_spawned(self) -> None:
        """Our player spawned; hooray!"""
        if not self.node:
            return
        self.node.opacity = 1.0
        self.update_for_lives()

    def handle_player_died(self) -> None:
        """Well poo; our player died."""
        if not self.node:
            return
        if self._show_death:
            bs.animate(
                self.node, 'opacity', {
                    0.00: 1.0,
                    0.05: 0.0,
                    0.10: 1.0,
                    0.15: 0.0,
                    0.20: 1.0,
                    0.25: 0.0,
                    0.30: 1.0,
                    0.35: 0.0,
                    0.40: 1.0,
                    0.45: 0.0,
                    0.50: 1.0,
                    0.55: 0.2
                })
            lives = self._player.lives
            if lives == 0:
                bs.timer(0.6, self.update_for_lives)

    def handlemessage(self, msg: Any) -> Any:
        if isinstance(msg, bs.DieMessage):
            if self.node:
                self.node.delete()
            return None
        return super().handlemessage(msg)


class Player(bs.Player['Team']):
    """Our player type for this game."""

    def __init__(self) -> None:
        self.lives = 0
        self.icons: list[Icon] = []


class Team(bs.Team[Player]):
    """Our team type for this game."""

    def __init__(self) -> None:
        self.survival_seconds: Optional[int] = None
        self.spawn_order: list[Player] = []


# ba_meta export bascenev1.GameActivity
class LasorTracerGame(bs.TeamGameActivity[Player, Team]):
    """Game type where last player(s) left alive win."""

    name = 'Laser Tracer'
    description = 'Last remaining alive wins.'
    scoreconfig = bs.ScoreConfig(label='Survived',
                                 scoretype=bs.ScoreType.SECONDS,
                                 none_is_winner=True)
    # Show messages when players die since it's meaningful here.
    announce_player_deaths = True

    allow_mid_activity_joins = False

    @classmethod
    def get_available_settings(
            cls, sessiontype: type[bs.Session]) -> list[babase.Setting]:
        settings = [
            bs.IntSetting(
                'Lives Per Player',
                default=1,
                min_value=1,
                max_value=10,
                increment=1,
            ),
            bs.IntChoiceSetting(
                'Time Limit',
                choices=[
                    ('None', 0),
                    ('1 Minute', 60),
                    ('2 Minutes', 120),
                    ('5 Minutes', 300),
                    ('10 Minutes', 600),
                    ('20 Minutes', 1200),
                ],
                default=0,
            ),
            bs.FloatChoiceSetting(
                'Respawn Times',
                choices=[
                    ('Shorter', 0.25),
                    ('Short', 0.5),
                    ('Normal', 1.0),
                    ('Long', 2.0),
                    ('Longer', 4.0),
                ],
                default=1.0,
            ),
            bs.BoolSetting('Epic Mode', default=False),
        ]
        if issubclass(sessiontype, bs.DualTeamSession):
            settings.append(bs.BoolSetting('Solo Mode', default=False))
            settings.append(
                bs.BoolSetting('Balance Total Lives', default=False))
        return settings

    @classmethod
    def supports_session_type(cls, sessiontype: type[bs.Session]) -> bool:
        return (issubclass(sessiontype, bs.DualTeamSession)
                or issubclass(sessiontype, bs.FreeForAllSession))

    @classmethod
    def get_supported_maps(cls, sessiontype: type[bs.Session]) -> list[str]:
        return ["Courtyard"]

    def __init__(self, settings: dict):
        super().__init__(settings)
        shared = SharedObjects.get()
        self._scoreboard = Scoreboard()
        self._start_time: Optional[float] = None
        self._vs_text: Optional[bs.Actor] = None
        self._round_end_timer: Optional[bs.Timer] = None
        self._epic_mode = bool(settings['Epic Mode'])
        self._lives_per_player = 1
        self._time_limit = float(settings['Time Limit'])
        self._balance_total_lives = bool(
            settings.get('Balance Total Lives', False))
        self._solo_mode = bool(settings.get('Solo Mode', False))

        # Base class overrides:
        self.slow_motion = self._epic_mode
        self.default_music = (bs.MusicType.EPIC
                              if self._epic_mode else bs.MusicType.SURVIVAL)
        self.laser_material = bs.Material()
        self.laser_material.add_actions(
            conditions=('they_have_material',
                        shared.player_material),
            actions=(('modify_part_collision', 'collide', True),
                     ('message', 'their_node', 'at_connect', bs.DieMessage()))
        )

    def get_instance_description(self) -> Union[str, Sequence]:
        return 'Last team standing wins.' if isinstance(
            self.session, bs.DualTeamSession) else 'Last one standing wins.'

    def get_instance_description_short(self) -> Union[str, Sequence]:
        return 'last team standing wins' if isinstance(
            self.session, bs.DualTeamSession) else 'last one standing wins'

    def on_player_join(self, player: Player) -> None:
        player.lives = self._lives_per_player

        if self._solo_mode:
            player.team.spawn_order.append(player)
            self._update_solo_mode()
        else:
            if player.lives > 0:
                self.spawn_player(player)

        if self.has_begun():
            self._update_icons()

    def on_begin(self) -> None:
        super().on_begin()
        self._start_time = bs.time()
        self.setup_standard_time_limit(self._time_limit)
        self.add_wall()
        self.create_laser()
        if self._solo_mode:
            self._vs_text = bs.NodeActor(
                bs.newnode('text',
                           attrs={
                               'position': (0, 105),
                               'h_attach': 'center',
                               'h_align': 'center',
                               'maxwidth': 200,
                               'shadow': 0.5,
                               'vr_depth': 390,
                               'scale': 0.6,
                               'v_attach': 'bottom',
                               'color': (0.8, 0.8, 0.3, 1.0),
                               'text': babase.Lstr(resource='vsText')
                           }))

        if (isinstance(self.session, bs.DualTeamSession)
                and self._balance_total_lives and self.teams[0].players
                and self.teams[1].players):
            if self._get_total_team_lives(
                    self.teams[0]) < self._get_total_team_lives(self.teams[1]):
                lesser_team = self.teams[0]
                greater_team = self.teams[1]
            else:
                lesser_team = self.teams[1]
                greater_team = self.teams[0]
            add_index = 0
            while (self._get_total_team_lives(lesser_team) <
                   self._get_total_team_lives(greater_team)):
                lesser_team.players[add_index].lives += 1
                add_index = (add_index + 1) % len(lesser_team.players)

        self._update_icons()
        bs.timer(1.0, self._update, repeat=True)

    def _update_solo_mode(self) -> None:
        for team in self.teams:
            team.spawn_order = [p for p in team.spawn_order if p]
            for player in team.spawn_order:
                assert isinstance(player, Player)
                if player.lives > 0:
                    if not player.is_alive():
                        self.spawn_player(player)
                    break

    def _update_icons(self) -> None:
        return

    def _get_spawn_point(self, player: Player) -> Optional[babase.Vec3]:
        del player
        if self._solo_mode:
            living_player = None
            living_player_pos = None
            for team in self.teams:
                for tplayer in team.players:
                    if tplayer.is_alive():
                        assert tplayer.node
                        ppos = tplayer.node.position
                        living_player = tplayer
                        living_player_pos = ppos
                        break
            if living_player:
                assert living_player_pos is not None
                player_pos = babase.Vec3(living_player_pos)
                points: list[tuple[float, babase.Vec3]] = []
                for team in self.teams:
                    start_pos = babase.Vec3(
                        self.map.get_start_position(team.id))
                    points.append(
                        ((start_pos - player_pos).length(), start_pos))
                points.sort(key=lambda x: x[0])
                return points[-1][1]
        return None

    def spawn_player(self, player: Player) -> bs.Actor:
        actor = self.spawn_player_spaz(player, self._get_spawn_point(player))
        actor.connect_controls_to_player(enable_punch=False,
                                         enable_bomb=False,
                                         enable_pickup=False)
        if not self._solo_mode:
            bs.timer(0.3, babase.Call(self._print_lives, player))

        for icon in player.icons:
            icon.handle_player_spawned()
        return actor

    def _print_lives(self, player: Player) -> None:
        from bascenev1lib.actor import popuptext

        if not player or not player.is_alive() or not player.node:
            return

        popuptext.PopupText('x' + str(player.lives - 1),
                            color=(1, 1, 0, 1),
                            offset=(0, -0.8, 0),
                            random_offset=0.0,
                            scale=1.8,
                            position=player.node.position).autoretain()

    def on_player_leave(self, player: Player) -> None:
        super().on_player_leave(player)
        player.icons = []

        if self._solo_mode:
            if player in player.team.spawn_order:
                player.team.spawn_order.remove(player)

        bs.timer(0, self._update_icons)

        if self._get_total_team_lives(player.team) == 0:
            assert self._start_time is not None
            player.team.survival_seconds = int(bs.time() - self._start_time)

    def _get_total_team_lives(self, team: Team) -> int:
        return sum(player.lives for player in team.players)

    def handlemessage(self, msg: Any) -> Any:
        if isinstance(msg, bs.PlayerDiedMessage):
            super().handlemessage(msg)
            player: Player = msg.getplayer(Player)

            player.lives -= 1
            if player.lives < 0:
                babase.print_error(
                    "Got lives < 0 in Elim; this shouldn't happen. solo:" +
                    str(self._solo_mode))
                player.lives = 0

            for icon in player.icons:
                icon.handle_player_died()

            if self._solo_mode or player.lives == 0:
                SpazFactory.get().single_player_death_sound.play()

            if player.lives == 0:
                if self._get_total_team_lives(player.team) == 0:
                    assert self._start_time is not None
                    player.team.survival_seconds = int(bs.time() -
                                                       self._start_time)
            else:
                if not self._solo_mode:
                    self.respawn_player(player)

            if self._solo_mode:
                player.team.spawn_order.remove(player)
                player.team.spawn_order.append(player)

    def _update(self) -> None:
        if self._solo_mode:
            for team in self.teams:
                team.spawn_order = [p for p in team.spawn_order if p]
                for player in team.spawn_order:
                    assert isinstance(player, Player)
                    if player.lives > 0:
                        if not player.is_alive():
                            self.spawn_player(player)
                            self._update_icons()
                        break

        if len(self._get_living_teams()) < 2:
            self._round_end_timer = bs.Timer(0.5, self.end_game)

    def _get_living_teams(self) -> list[Team]:
        return [
            team for team in self.teams
            if len(team.players) > 0 and any(player.lives > 0
                                             for player in team.players)
        ]

    def end_game(self) -> None:
        if self.has_ended():
            return
        results = bs.GameResults()
        self._vs_text = None
        for team in self.teams:
            results.set_team_score(team, team.survival_seconds)
        self.end(results=results)

    def add_wall(self):
        shared = SharedObjects.get()
        pwm = bs.Material()
        pwm.add_actions(
            actions=('modify_part_collision', 'friction', 0.0))

        pwm.add_actions(
            conditions=('they_have_material',
                        shared.player_material),
            actions=('modify_part_collision', 'collide', True))
        cmesh = bs.getcollisionmesh('courtyardPlayerWall')
        self.player_wall = bs.newnode(
            'terrain',
            attrs={
                'collision_mesh': cmesh,
                'affect_bg_dynamics': False,
                'materials': [pwm]
            })

    def create_laser(self) -> None:
        bs.timer(6.0, babase.Call(self.LRlaser, True))
        bs.timer(7.0, babase.Call(self.UDlaser, True))
        bs.timer(30.0, babase.Call(self.create_laser))

    def LRlaser(self, left: bool):
        ud_1_r = bs.newnode('region', attrs={'position': (-5, 2.6, 0), 'scale': (
            0.1, 0.6, 15), 'type': 'box', 'materials': [self.laser_material]})
        x = -6.0
        for i in range(0, 30):
            x = x + 0.4
            node = bs.newnode('shield', owner=ud_1_r, attrs={
                              'color': (1, 0, 0), 'radius': 0.28})
            mnode = bs.newnode('math',
                               owner=ud_1_r,
                               attrs={
                                   'input1': (0, 0.0, x),
                                   'operation': 'add'
                               })
            ud_1_r.connectattr('position', mnode, 'input2')
            mnode.connectattr('output', node, 'position')

        _rcombine = bs.newnode('combine',
                               owner=ud_1_r,
                               attrs={
                                   'input1': 2.6,
                                   'input2': -2.0,
                                   'size': 3
                               })
        if left:
            x1, x2 = -10.0, 10.0
        else:
            x1, x2 = 10.0, -10.0
        bs.animate(_rcombine, 'input0', {
            0: x1,
            20: x2
        })
        _rcombine.connectattr('output', ud_1_r, 'position')
        bs.timer(20.0, ud_1_r.delete)
        t = random.randrange(7, 13)
        bs.timer(float(t), babase.Call(self.LRlaser, bool(random.randrange(0, 2))))

    def UDlaser(self, up: bool):
        ud_2_r = bs.newnode('region', attrs={'position': (-3, 2.6, -6), 'scale': (
            20, 0.6, 0.1), 'type': 'box', 'materials': [self.laser_material]})
        x = -6.0
        for i in range(0, 40):
            x = x + 0.4
            node = bs.newnode('shield', owner=ud_2_r, attrs={
                              'color': (1, 0, 0), 'radius': 0.28})
            mnode = bs.newnode('math',
                               owner=ud_2_r,
                               attrs={
                                   'input1': (x, 0.0, 0),
                                   'operation': 'add'
                               })
            ud_2_r.connectattr('position', mnode, 'input2')
            mnode.connectattr('output', node, 'position')

        _rcombine = bs.newnode('combine',
                               owner=ud_2_r,
                               attrs={
                                   'input0': -2.0,
                                   'input1': 2.6,
                                   'size': 3
                               })
        if up:
            x1, x2 = -9.0, 6.0
        else:
            x1, x2 = 6.0, -9.0
        bs.animate(_rcombine, 'input2', {
            0: x1,
            17: x2
        })
        _rcombine.connectattr('output', ud_2_r, 'position')

        bs.timer(17.0, ud_2_r.delete)
        t = random.randrange(6, 13)
        bs.timer(float(t), babase.Call(self.UDlaser, bool(random.randrange(0, 2))))