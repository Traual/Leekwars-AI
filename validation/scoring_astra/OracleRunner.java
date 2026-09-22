package validation.astra;

import java.io.File;
import com.leekwars.generator.Generator;
import com.leekwars.generator.Util;
import com.leekwars.generator.fight.Fight;
import com.leekwars.generator.scenario.Scenario;
import com.leekwars.generator.test.LocalDbRegisterManager;
import com.leekwars.generator.test.LocalTrophyManager;
import com.leekwars.generator.util.Json;
import leekscript.compiler.LeekScript;
import leekscript.compiler.resolver.NativeFileSystem;
import tools.jackson.databind.node.ObjectNode;

/** Reads the actual terminal state through a passive FightListener. No engine patch. */
public final class OracleRunner {
    public static void main(String[] args) {
        LeekScript.setFileSystem(new NativeFileSystem());
        var generator = new Generator();
        generator.setCache(true);
        for (int i = 0; i < args.length; ++i) {
            ObjectNode result = Json.createObject();
            try {
                var file = new File(args[i]);
                var scenario = Scenario.fromFile(file);
                var source = Json.parseObject(Util.readFile(file));
                scenario.type = source.get("fight_type").intValue();
                scenario.context = source.get("fight_context").intValue();
                scenario.map = (ObjectNode) source.get("map");
                Fight[] observed = new Fight[1];
                var outcome = generator.runScenario(scenario, fight -> observed[0] = fight,
                    new LocalDbRegisterManager(), new LocalTrophyManager());
                if (outcome.exception != null) result.put("exception", outcome.exception.toString());
                result.set("outcome", outcome.toJson());
                result.put("winner", outcome.winner);
                if (observed[0] == null) throw new IllegalStateException("no fight observed");
                var terminal = result.putArray("terminal");
                for (var entity : observed[0].getState().getAllEntities(true)) {
                    var row = terminal.addArray();
                    row.add(entity.getFId());
                    row.add(entity.getLife());
                    row.add(entity.getTotalLife());
                    row.add(entity.getTeam());
                }
            } catch (Throwable error) {
                result.put("runner_error", error.toString());
            }
            System.out.println("__FIDELITE__\t" + i + "\t" + result.toString());
            System.out.flush();
        }
    }
}
